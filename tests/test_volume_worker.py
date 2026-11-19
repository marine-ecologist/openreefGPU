import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from worker import handler as worker


@pytest.fixture
def mounted(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENREEF_VOLUME_ROOT", str(tmp_path))
    monkeypatch.setenv("OPENREEF_VERSION", "0.6.5")
    monkeypatch.setenv("OPENREEF_GPU_VERSION", "0.6.5-gpu.10")
    source = tmp_path / "openreef-sources/original/A"
    source.mkdir(parents=True)
    for i in range(2):
        (source / f"frame_{i:06d}.jpg").write_bytes(b"source photograph")
    monkeypatch.setattr(
        worker, "create_storage_client", Mock(side_effect=AssertionError("Drive used"))
    )

    def command(dataset, **kwargs):
        return [
            sys.executable,
            "-c",
            'from pathlib import Path; import sys; p=Path(sys.argv[1])/"models"; '
            'p.mkdir(); (p/"test_textured_mesh_compact.glb").write_bytes(b"glb"); '
            'print("pipeline test")',
            str(dataset),
        ]

    monkeypatch.setattr(worker, "pipeline_command", command)
    return tmp_path, source


def payload(**values):
    return {
        "schema_version": 1,
        "openreef_version": "0.6.5",
        "openreef_gpu_version": "0.6.5-gpu.10",
        "volume": {"folder": "original/A"},
        **values,
    }


def test_dispatch_uses_links_persists_outputs_and_isolates_reruns(mounted):
    mount, source = mounted
    first = worker.handler({"input": payload(pipeline={"max_image_size": 1600})})
    second = worker.handler({"input": payload()})
    assert first["state"] == second["state"] == "completed"
    assert first["dataset"] != second["dataset"]
    assert first["filesDownloaded"] == 0
    image = Path(first["dataset"]) / "images/frame_000000.jpg"
    assert image.is_symlink() and image.resolve() == source / image.name
    assert image.read_bytes() == b"source photograph"
    assert Path(first["artifacts"]["model"]).read_bytes() == b"glb"
    assert json.loads(Path(first["statusPath"]).read_text())["state"] == "completed"
    assert "pipeline test" in Path(first["logPath"]).read_text()
    assert first["command"][-2:] == ["--max-image-size", "1600"]


@pytest.mark.parametrize("folder", ["../../outside", "/etc", "../openreef-runs"])
def test_rejects_escaping_source_paths(mounted, folder):
    with pytest.raises(ValueError):
        worker.handler({"input": payload(volume={"folder": folder})})


def test_rejects_source_file_symlink_escape(mounted, tmp_path):
    _, source = mounted
    outside = tmp_path / "secret.jpg"
    outside.write_bytes(b"private")
    (source / "escape.jpg").symlink_to(outside)
    with pytest.raises(ValueError, match="escapes"):
        worker.handler({"input": payload()})


def test_rejects_escaped_run_root(mounted):
    mount, _ = mounted
    (mount / "openreef-runs").symlink_to(mount.parent)
    with pytest.raises(ValueError, match="escapes"):
        worker.handler({"input": payload()})


@pytest.mark.parametrize("options", [{"cores": True}, {"cores": 0}, {"command": "echo unsafe"}])
def test_rejects_invalid_pipeline_options(mounted, options):
    with pytest.raises(ValueError):
        worker.handler({"input": payload(pipeline=options)})


def test_version_mismatch_rejected(mounted):
    with pytest.raises(ValueError, match="version"):
        worker.handler({"input": payload(openreef_gpu_version="other")})


def test_failure_keeps_status_logs_and_source(mounted, monkeypatch):
    mount, source = mounted
    monkeypatch.setattr(
        worker,
        "pipeline_command",
        lambda *a: [sys.executable, "-c", 'print("native failure evidence"); raise SystemExit(7)'],
    )
    with pytest.raises(RuntimeError, match="code 7"):
        worker.handler({"input": payload()})
    (status,) = (mount / "openreef-runs").glob("*/job.json")
    assert json.loads(status.read_text())["state"] == "failed"
    assert "native failure evidence" in status.with_name("pipeline.log").read_text()
    assert (source / "frame_000000.jpg").read_bytes() == b"source photograph"


def test_partial_stage_run_needs_no_model(mounted, monkeypatch):
    monkeypatch.setattr(
        worker, "pipeline_command", lambda *a: [sys.executable, "-c", 'print("features only")']
    )
    result = worker.handler({"input": payload(pipeline={"stages": "features"})})
    assert result["state"] == "completed" and result["artifacts"] == {}


def test_stereo_uses_links_and_preserves_camera_identity(mounted, monkeypatch):
    from PIL import Image

    # Load exactly the stereo implementation shipped by the worker Docker patch.
    patch = (
        Path(__file__).resolve().parents[1] / "worker/patches/openreef-stereo.patch"
    ).read_text()
    body = patch.split("@@ -0,0 +1,")[1].split("\n", 1)[1]
    code = "\n".join(line[1:] for line in body.splitlines() if line.startswith("+"))
    import types

    stereo = types.ModuleType("openreef.pipeline.stereo")
    monkeypatch.setitem(sys.modules, stereo.__name__, stereo)
    exec(compile(code, "<worker stereo patch>", "exec"), stereo.__dict__)
    mount, left = mounted
    right = left.with_name("B")
    right.mkdir()
    for folder in (left, right):
        for path in left.iterdir():
            Image.new("RGB", (640, 480)).save(folder / path.name)
    profile = {
        "schemaVersion": 1,
        "translationUnit": "metres",
        "cameras": [
            {
                "id": c,
                "model": "PINHOLE",
                "width": 640,
                "height": 480,
                "params": [500, 500, 320, 240],
            }
            for c in ("left", "right")
        ],
        "rightFromLeft": {
            "rotation": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
            "translation": [-0.15, 0, 0],
        },
    }
    root = mount / "openreef-sources"
    (root / "rig.json").write_text(json.dumps(profile))
    (root / "times.json").write_text(
        json.dumps(
            {c: {f"frame_{i:06d}.jpg": float(i) for i in range(2)} for c in ("left", "right")}
        )
    )
    result = worker.handler(
        {
            "input": payload(
                volume={
                    "folder": "original/A",
                    "right_folder": "original/B",
                    "profile": "rig.json",
                    "timestamps": "times.json",
                }
            )
        }
    )
    assert result["imageCount"] == 4
    dataset = Path(result["dataset"])
    for path in (dataset / "images").rglob("*.jpg"):
        assert path.is_symlink()
    assert "--stereo" in result["command"]
    assert (dataset / "stereo/frames.json").is_file()


def test_consecutive_subset_links_only_requested_images(mounted):
    _, source = mounted
    for i in range(2, 5):
        (source / f"frame_{i:06d}.jpg").write_bytes(b"image")
    result = worker.handler(
        {"input": payload(volume={"folder": "original/A", "start": 1, "count": 2})}
    )
    assert sorted(p.name for p in (Path(result["dataset"]) / "images").iterdir()) == [
        "frame_000001.jpg",
        "frame_000002.jpg",
    ]
    assert len(list(source.iterdir())) == 5


def test_submitter_sends_volume_payload_without_cloud_job(mounted, monkeypatch, capsys):
    import importlib.util

    script = Path(__file__).resolve().parents[1] / "scripts/submit-volume.py"
    spec = importlib.util.spec_from_file_location("submit_volume_test", script)
    submit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(submit)
    sent = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"id":"runpod-job","status":"IN_QUEUE"}'

    def urlopen(request, timeout):
        sent.append(json.loads(request.data))
        assert request.get_header("Authorization") == "Bearer test-secret"
        return Response()

    monkeypatch.setenv("RUNPOD_API_KEY", "test-secret")
    monkeypatch.setattr(submit.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(script),
            "--endpoint",
            "example123",
            "--folder",
            "original/A",
            "--folder",
            "original/B",
            "--count",
            "200",
            "--cores",
            "16",
        ],
    )
    assert submit.main() == 0
    assert [r["input"]["volume"]["folder"] for r in sent] == ["original/A", "original/B"]
    assert sent[0]["input"]["volume"]["count"] == 200
    assert sent[0]["input"]["pipeline"] == {"cores": 16}
    assert "job_id" not in sent[0]["input"]
    assert "test-secret" not in capsys.readouterr().out


def test_dispatch_supports_runpy_container_start_command(mounted, monkeypatch):
    import runpy
    from types import SimpleNamespace

    registered = {}
    monkeypatch.setitem(
        sys.modules,
        "runpod",
        SimpleNamespace(
            serverless=SimpleNamespace(start=lambda settings: registered.update(settings))
        ),
    )
    runpy.run_path(str(Path(worker.__file__)), run_name="__main__")
    result = registered["handler"]({"input": payload()})
    assert result["state"] == "completed"
    assert result["filesDownloaded"] == 0
