"""Mounted storage for browser-submitted RunPod video jobs."""

import fcntl
import json
import os
import shutil
import tempfile
from pathlib import Path

from worker.volume import confined


class VolumeJobStorage:
    def __init__(self):
        mount = Path(os.getenv("OPENREEF_VOLUME_ROOT", "/runpod-volume")).resolve()
        if not mount.is_dir():
            raise ValueError("RunPod network volume is not mounted")
        self.root = confined(mount, "openreef-jobs")

    def path(self, key):
        return confined(self.root, key)

    def job_path(self, job_id):
        return job_id

    def get_json(self, key):
        return json.loads(self.path(key).read_text())

    def put_bytes(self, key, value, _content_type):
        self._write(key, lambda temporary: temporary.write_bytes(value))

    def put_file(self, key, source):
        self._write(key, lambda temporary: shutil.copyfile(source, temporary))

    def _write(self, key, write):
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as file:
            temporary = Path(file.name)
        try:
            write(temporary)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)

    def download_folder(self, key, destination, files, report):
        """Use verified uploaded files in place via links in the job workspace."""
        paths = [(file, self.path(f"{key}/{file['name']}")) for file in files]
        destination.mkdir(parents=True, exist_ok=True)
        total = sum(file["size"] for file in files)
        done = 0
        for index, (file, path) in enumerate(paths, 1):
            if not path.is_file() or path.stat().st_size != file["size"]:
                raise ValueError(f"Camera {file['cameraId']} failed volume size verification")
            (destination / file["name"]).symlink_to(path)
            done += file["size"]
            report(index, len(files), done, total, file["name"], index)
        return len(files)

    def purge(self, job_id):
        folder = self.path(job_id)
        if folder != self.root / job_id:
            raise ValueError("Refusing to purge a redirected job folder")
        with (folder / ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            key = f"{job_id}/job.json"
            job = self.get_json(key)
            if job["state"] == "purged":
                return job
            if job["state"] != "completed" or not job.get("result", {}).get("bundle"):
                raise ValueError("Only completed bundled jobs can be purged")
            # Keep the small history record. Never descend into shared sources or another job.
            for name in ("workspace", "input", "output", "diagnostics"):
                target = folder / name
                if target.is_symlink():
                    target.unlink()
                elif target.exists():
                    shutil.rmtree(target)
            for key_name in (
                "result",
                "stageAssets",
                "diagnostics",
                "purgeJobId",
                "error",
                "runpodJobId",
                "stereoVideo",
                "files",
                "syncWarnings",
                "transfers",
                "excludedPairs",
                "pairCount",
                "reviewed",
            ):
                job.pop(key_name, None)
            job.update(state="purged", stage="Downloaded and purged", progress=100)
            self.put_bytes(key, json.dumps(job).encode(), "application/json")
            return job
