"""Portable archive and worker regression tests (also runnable with unittest)."""

import copy
import io
import json
import os
import sys
import tarfile
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from worker import handler as worker


class FullProjectTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.dataset = self.root / "survey"
        self.dataset.mkdir()
        for name in ("images", "colmap/sparse/0", "openmvs", "models", "logs"):
            (self.dataset / name).mkdir(parents=True, exist_ok=True)
        (self.dataset / "colmap/database.db").write_bytes(b"database")
        (self.dataset / "colmap/sparse/0/cameras.bin").write_bytes(b"cameras")
        (self.dataset / "openmvs/scene.mvs").write_bytes(b"scene")
        (self.dataset / "logs/pipeline.log").write_text("processing log")
        (self.dataset / "images/one.jpg").write_bytes(b"photo")
        self.job = {"id": "test-job", "files": [{"name": "one.jpg", "size": 5}]}

    def test_compact_defaults_and_full_pipeline_outputs(self):
        self.assertEqual(worker.project_options({}), ("compact", False))
        self.assertIn("--no-dense-original", worker.pipeline_command(self.dataset))
        full = worker.pipeline_command(self.dataset, full_project=True)
        self.assertIn("--dense-original", full)
        self.assertIn("--dense-compact", full)
        self.assertNotIn("--no-dense-original", full)
        for options in ({"outputMode": "bad"}, {"outputMode": []},
                        {"includeSourceImages": True}, {"includeSourceImages": "false"}):
            with self.assertRaises(ValueError):
                worker.project_options(options)

    def test_archive_retains_workflow_without_original_photographs(self):
        archive = worker.archive_project(self.dataset, self.job, False)
        with tarfile.open(archive) as source:
            names = source.getnames()
            self.assertIn("survey/colmap/database.db", names)
            self.assertIn("survey/colmap/sparse/0/cameras.bin", names)
            self.assertIn("survey/openmvs/scene.mvs", names)
            self.assertIn("survey/logs/pipeline.log", names)
            self.assertFalse(any(name.startswith("survey/images") for name in names))
            metadata = json.load(source.extractfile("survey/cloud-run.json"))
            self.assertFalse(metadata["sourceImagesIncluded"])
            self.assertEqual(metadata["sourceFiles"], self.job["files"])

    def test_archive_embeds_cached_images_and_relocates_model_aliases(self):
        cached = self.root / "cached.jpg"
        cached.write_bytes(b"cached photograph")
        (self.dataset / "images/cached.jpg").symlink_to(cached)
        target = self.dataset / "openmvs/mesh.ply"
        target.write_bytes(b"mesh")
        (self.dataset / "models/mesh.ply").symlink_to(target)
        archive = worker.archive_project(self.dataset, self.job, True)
        extracted = self.root / "downloaded"
        with tarfile.open(archive) as source:
            info = source.getmember("survey/images/cached.jpg")
            self.assertTrue(info.isfile())
            self.assertEqual(source.extractfile(info).read(), cached.read_bytes())
            alias = source.getmember("survey/models/mesh.ply")
            self.assertEqual(alias.linkname, "../openmvs/mesh.ply")
            source.extractall(extracted, filter="data")
        self.assertEqual((extracted / "survey/models/mesh.ply").read_bytes(), b"mesh")

    def test_archive_rejects_unrelated_external_links_and_insufficient_disk(self):
        external = self.root / "unrelated"
        external.write_text("not part of project")
        link = self.dataset / "models/external"
        link.symlink_to(external)
        with self.assertRaisesRegex(RuntimeError, "external project link"):
            worker.archive_project(self.dataset, self.job, False)
        link.unlink()
        with patch.object(worker.shutil, "disk_usage", return_value=types.SimpleNamespace(free=0)):
            with self.assertRaisesRegex(RuntimeError, "Insufficient temporary disk"):
                worker.archive_project(self.dataset, self.job, False)

    def test_processing_log_is_saved(self):
        command = [sys.executable, "-c", "print('Feature extraction'); print('finished')"]
        stages = []
        with patch.object(worker, "pipeline_command", return_value=command):
            worker.run_pipeline(self.dataset, lambda name, value: stages.append(name))
        self.assertIn("finished", (self.dataset / "logs/pipeline.log").read_text())
        self.assertEqual(stages, ["Feature extraction"])

    def test_handler_uploads_full_archive_before_temporary_cleanup(self):
        job_id = "12345678-1234-4123-8123-123456789abc"
        job = {"id": job_id, "schemaVersion": 1, "name": "survey", "state": "queued",
               "outputMode": "full", "includeSourceImages": True,
               "files": [{"name": "one.jpg", "size": 5}],
               "versions": {"openreef": "0.6.5", "openreefGPU": "0.6.5-gpu.6"}}
        storage = FakeStorage(job)
        dataset_paths = []

        def pipeline(dataset, progress, *, full_project=False):
            self.assertTrue(full_project)
            dataset_paths.append(dataset)
            (dataset / "openmvs").mkdir()
            (dataset / "openmvs/scene_mesh_compact_textured.glb").write_bytes(b"glb")
            (dataset / "openmvs/scene_mesh.ply").write_bytes(b"original mesh")

        env = {"OPENREEF_VERSION": "0.6.5", "OPENREEF_GPU_VERSION": "0.6.5-gpu.6"}
        event = {"input": {"job_id": job_id, "schema_version": 1,
                           "openreef_version": "0.6.5", "openreef_gpu_version": "0.6.5-gpu.6"}}
        with patch.dict(os.environ, env), patch.dict(sys.modules, {"openreef": types.SimpleNamespace(__version__="0.6.5")}), patch.object(worker, "create_storage_client", return_value=storage), patch.object(worker, "run_pipeline", side_effect=pipeline):
            result = worker.handler(event)
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["result"]["project"], "full-project.tar.gz")
        self.assertFalse(dataset_paths[0].exists())
        content = storage.files[f"jobs/{job_id}/output/full-project.tar.gz"]
        with tarfile.open(fileobj=io.BytesIO(content)) as archive:
            self.assertIn("survey/openmvs/scene_mesh.ply", archive.getnames())
            self.assertIn("survey/images/one.jpg", archive.getnames())


class FakeStorage:
    def __init__(self, job):
        self.job = job
        self.files = {}

    def job_path(self, job_id):
        return f"jobs/{job_id}"

    def get_json(self, path):
        return copy.deepcopy(self.job)

    def put_bytes(self, path, content, content_type):
        self.files[path] = content
        if path.endswith("/job.json"):
            self.job = json.loads(content)

    def put_file(self, path, source):
        self.files[path] = source.read_bytes()

    def download_folder(self, source, destination, expected_names, on_progress):
        destination.mkdir(parents=True)
        (destination / "one.jpg").write_bytes(b"photo")
        return 1


if __name__ == "__main__":
    unittest.main()
