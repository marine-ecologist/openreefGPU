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
        if settings.get("reuseSparseModels"):
            assert Path(settings["reuseSparseModels"]).name == "sparse"
            (dataset / "joint/diagnostics.json").write_text(
                json.dumps({"merge": {"improvedLargest": False, "renderSkipped": True}})
            )
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

    previous = Path(result["dataset"])
    (previous / "colmap/sparse").mkdir(parents=True)
    with sqlite3.connect(previous / "colmap/database.db") as conn:
        conn.execute("CREATE TABLE retained (value INTEGER)")
    (previous / "joint/diagnostics.json").write_text(
        json.dumps({"verifiedCrossCameraPairs": 1, "sparseModels": [{"path": "0"}]})
    )
    payload = {
        "schema_version": 1,
        "openreef_version": "0.6.5",
        "openreef_gpu_version": "0.6.5-gpu.10",
        "volume": {
            "folder": "A",
            "right_folder": "B",
            "mode": "joint",
            "resume_run": result["id"],
            "merge_components": True,
        },
        "pipeline": {"max_image_size": 1600, "max_resolution": 1600, "cores": 16},
    }
    recovered = worker.handler({"input": payload})
    assert recovered["state"] == "completed" and recovered["artifacts"] == {}
    assert recovered["resumedFrom"] == result["id"]
    (tmp_path / "openreef-sources/A/frame_000000.jpg").write_bytes(b"changed source size")
    with pytest.raises(ValueError, match="manifest has changed"):
        worker.handler({"input": payload})


def test_database_recovery_is_independent_and_keeps_wal_records(tmp_path):
    from worker.joint import copy_database

    source, target = tmp_path / "old.db", tmp_path / "new.db"
    with sqlite3.connect(source) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE saved (value INTEGER)")
        conn.execute("INSERT INTO saved VALUES (123)")
        conn.commit()
        copy_database(source, target)
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT value FROM saved").fetchone() == (123,)
        conn.execute("DELETE FROM saved")
    with sqlite3.connect(source) as conn:
        assert conn.execute("SELECT value FROM saved").fetchone() == (123,)


def test_mapping_checkpoints_preserve_optimization_defaults(tmp_path):
    from worker.joint import mapper_command

    command = mapper_command(tmp_path, 16, tmp_path / "initial")
    assert command[command.index("--Mapper.snapshot_frames_freq") + 1] == "100"
    assert command[command.index("--Mapper.ba_global_frames_ratio") + 1] == "1.5"
    assert command[-2:] == ["--input_path", str(tmp_path / "initial")]
    assert not any("max_num_iterations" in flag or "max_refinements" in flag for flag in command)


def test_recovery_selects_largest_complete_snapshot(tmp_path):
    import struct

    from worker.joint import largest_snapshot

    for name, count in [("older", 400), ("newer", 100)]:
        model = tmp_path / name
        model.mkdir()
        for component in ("cameras.bin", "points3D.bin"):
            (model / component).touch()
        (model / "images.bin").write_bytes(struct.pack("<Q", count))
    incomplete = tmp_path / "incomplete"
    incomplete.mkdir()
    (incomplete / "images.bin").write_bytes(struct.pack("<Q", 2000))
    assert largest_snapshot(tmp_path).name == "older"


def _write_model_names(model, names):
    import struct

    model.mkdir(exist_ok=True)
    with (model / "images.bin").open("wb") as stream:
        stream.write(struct.pack("<Q", len(names)))
        for name in sorted(names):
            stream.write(bytes(64) + name.encode() + b"\0" + struct.pack("<Q", 0))


def test_component_merge_can_join_through_a_bridge(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import worker.merge as merging

    groups = [set("abcde"), set("defgh"), set("bcdfg")]
    models = [tmp_path / str(i) for i in range(3)]
    for model, names in zip(models, groups, strict=True):
        _write_model_names(model, names)

    def native_merge(command, check):
        first = Path(command[command.index("--input_path1") + 1])
        second = Path(command[command.index("--input_path2") + 1])
        target = Path(command[command.index("--output_path") + 1])
        assert command[-2:] == ["--max_reproj_error", "8"]
        _write_model_names(target, merging.image_names(first) | merging.image_names(second))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(merging.subprocess, "run", native_merge)
    merged, report = merging.merge_components(models, tmp_path / "attempts")
    assert merging.image_names(merged) == set("abcdefgh")
    assert report["acceptedMerges"] == 2 and report["improvedLargest"]
    assert merging.image_names(models[0]) == groups[0]


def test_rejected_component_alignment_never_forces_a_merge(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import worker.merge as merging

    models = [tmp_path / "a", tmp_path / "b"]
    for model, names in zip(models, [set("abcde"), set("abcfg")], strict=True):
        _write_model_names(model, names)
    monkeypatch.setattr(
        merging.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1)
    )
    _, report = merging.merge_components(models, tmp_path / "attempts")
    assert report["acceptedMerges"] == 0 and not report["improvedLargest"]
    assert report["attempts"][0]["sharedImages"] == 3


def test_image_index_rejects_incomplete_observations(tmp_path):
    import struct

    from worker.merge import image_names

    _write_model_names(tmp_path, {"camera-A/frame_000001.jpg"})
    binary = tmp_path / "images.bin"
    data = binary.read_bytes()
    binary.write_bytes(data[:-8] + struct.pack("<Q", 10))
    with pytest.raises(ValueError, match="observations"):
        image_names(tmp_path)
