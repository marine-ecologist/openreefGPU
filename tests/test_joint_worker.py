import json
import sqlite3
import sys
from pathlib import Path

import pytest

from worker.joint import candidate_pairs, verify_cameras


def test_candidates_include_cross_camera_and_temporal_edges_without_quadratic_matching():
    names = [f"camera-{camera}/frame_{i:06d}.jpg" for i in range(100) for camera in "AB"]
    edges = candidate_pairs(names)
    assert ("camera-A/frame_000000.jpg", "camera-B/frame_000000.jpg") in edges
    assert ("camera-A/frame_000000.jpg", "camera-A/frame_000001.jpg") in edges
    assert ("camera-A/frame_000000.jpg", "camera-B/frame_000001.jpg") in edges
    assert len(edges) < len(names) * (len(names) - 1) // 2
    assert len(edges) == len(set(edges))


@pytest.mark.parametrize("merged", [False, True])
def test_camera_identity_check_detects_accidental_shared_intrinsics(tmp_path, merged):
    database = tmp_path / "database.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE images (name TEXT, camera_id INTEGER)")
        conn.executemany(
            "INSERT INTO images VALUES (?, ?)",
            [("camera-A/frame_1.jpg", 1), ("camera-B/frame_1.jpg", 1 if merged else 2)],
        )
    if merged:
        with pytest.raises(RuntimeError, match="merged"):
            verify_cameras(database)
    else:
        assert verify_cameras(database)["cameras"] == {"camera-A": 1, "camera-B": 2}


def test_joint_volume_links_all_images_without_requiring_calibration(tmp_path, monkeypatch):
    import worker.volume as volume
    from worker import handler as worker

    monkeypatch.setenv("OPENREEF_VOLUME_ROOT", str(tmp_path))
    monkeypatch.setenv("OPENREEF_VERSION", "0.6.5")
    monkeypatch.setenv("OPENREEF_GPU_VERSION", "0.6.5-gpu.10")
    for c in "AB":
        folder = tmp_path / "openreef-sources" / c
        folder.mkdir(parents=True)
        for i in range(2):
            (folder / f"frame_{i:06d}.jpg").write_bytes(b"image")
    original = volume.subprocess.Popen

    def fake_joint(command, **kwargs):
        dataset = Path(command[-1])
        assert command[-3:-1] == ["-m", "worker.joint"]
        assert not (dataset / "stereo").exists()
        settings = json.loads((dataset / "joint/settings.json").read_text())
        assert len(settings["images"]) == 4
        assert "--stereo" not in settings["pipelineCommand"]
        script = (
            'from pathlib import Path; import sys; p=Path(sys.argv[1])/"models"; '
            'p.mkdir(); (p/"test_textured_mesh_compact.glb").write_bytes(b"glb")'
        )
        return original([sys.executable, "-c", script, str(dataset)], **kwargs)

    monkeypatch.setattr(volume.subprocess, "Popen", fake_joint)
    result = worker.handler(
        {
            "input": {
                "schema_version": 1,
                "openreef_version": "0.6.5",
                "openreef_gpu_version": "0.6.5-gpu.10",
                "volume": {"folder": "A", "right_folder": "B", "mode": "joint"},
                "pipeline": {"max_image_size": 1600, "max_resolution": 1600, "cores": 16},
            }
        }
    )
    assert result["state"] == "completed"
    assert result["imageCount"] == 4 and result["filesDownloaded"] == 0
    assert result["metricCalibration"] is False
    for c in "AB":
        assert (Path(result["dataset"]) / f"images/camera-{c}/frame_000000.jpg").is_symlink()
