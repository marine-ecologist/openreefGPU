import os
from pathlib import Path

import cv2
import numpy as np
import pytest
from geometry import (
    baseline_report,
    center,
    orient,
    project,
    read_model,
    solve,
    write_model,
)


def scene():
    rng = np.random.default_rng(42)
    cameras = {
        1: {
            "width": 5312,
            "height": 2988,
            "p": np.array([4219, 4219, 2656, 1494, 0.02, 0.001, 0, 0, 0.0001, 0, 0, 0.0]),
        },
        2: {
            "width": 3840,
            "height": 2160,
            "p": np.array([3050, 3050, 1920, 1080, 0.02, 0.001, 0, 0, 0.0001, 0, 0, 0.0]),
        },
    }
    points = {
        i + 1: {
            "xyz": np.array(
                [rng.uniform(-0.8, 0.8), rng.uniform(-0.6, 0.6), rng.uniform(1.7, 2.5)]
            ),
            "rgb": [100, 150, 180],
        }
        for i in range(80)
    }
    images = {}
    for station in range(5):
        for side, cam in [("A", 1), ("B", 2)]:
            key = 2 * station + cam
            c = np.array([station * 0.13, 0.351 * (cam - 1), 0.03 * np.sin(station)])
            R = cv2.Rodrigues(
                np.array([0.01 * station, 0.005 * station, 0.01 * (cam - 1) * (station + 1)])
            )[0]
            im = {
                "R": R,
                "t": -R @ c,
                "camera": cam,
                "name": f"camera-{side}/frame_{station:05d}.jpg",
            }
            xyz = np.array([v["xyz"] for v in points.values()])
            xy = project(im, cameras[cam], xyz)
            im["obs"] = np.column_stack((xy, np.array(list(points))))
            images[key] = im
    return cameras, images, points


def test_projection_matches_opencv_fisheye_radial_convention():
    cameras, images, points = scene()
    cam = cameras[1]
    im = images[1]
    xyz = np.array([p["xyz"] for p in points.values()])
    K = np.array([[cam["p"][0], 0, cam["p"][2]], [0, cam["p"][1], cam["p"][3]], [0, 0, 1.0]])
    reference, _ = cv2.fisheye.projectPoints(
        xyz.reshape(-1, 1, 3), cv2.Rodrigues(im["R"])[0], im["t"], K, cam["p"][[4, 5, 8, 9]]
    )
    np.testing.assert_allclose(project(im, cam, xyz), reference.reshape(-1, 2), atol=1e-8)


def test_pose_roundtrip_and_plane_orientation_preserve_geometry(tmp_path):
    cameras, images, points = scene()
    old = project(images[4], cameras[2], np.array([p["xyz"] for p in points.values()]))
    distances = [np.linalg.norm(center(images[a]) - center(images[b])) for a, b in [(1, 2), (3, 4)]]
    report = orient(images, points)
    assert not report["flattened"]
    new = project(images[4], cameras[2], np.array([p["xyz"] for p in points.values()]))
    np.testing.assert_allclose(old, new, atol=1e-8)
    for before, (a, b) in zip(distances, [(1, 2), (3, 4)], strict=True):
        assert np.linalg.norm(center(images[a]) - center(images[b])) == pytest.approx(before)
    model = tmp_path / "model"
    write_model(model, cameras, images, points)
    _, restored, _ = read_model(model)
    for key in images:
        np.testing.assert_allclose(restored[key]["R"], images[key]["R"], atol=1e-8)
    assert max(center(im)[2] for im in images.values()) > 0


def test_native_distance_bundle_adjustment_recovers_metric_baselines(tmp_path):
    binary = Path(
        os.environ.get(
            "PAIRED_BA_BINARY", "/private/tmp/openreef-paired-native-build/paired-baseline-ba"
        )
    )
    if not binary.is_file():
        pytest.skip("Build the native Ceres test binary first")
    cameras, images, points = scene()
    for station in range(5):
        im = images[2 * station + 2]
        c = center(im)
        c[1] *= 0.85 + 0.075 * station
        im["t"] = -im["R"] @ c
    before = baseline_report(images)
    result = solve(
        binary, tmp_path / "fit", cameras, images, points, bars=True, gpu=False, iterations=80
    )
    after = baseline_report(images)
    assert result["finalCost"] < result["initialCost"] * 0.01
    assert after["medianMetres"] == pytest.approx(0.351, abs=0.001)
    assert (
        after["medianAbsoluteRelativeDeviation"] < before["medianAbsoluteRelativeDeviation"] * 0.1
    )
    # Both cameras keep independent per-frame rotations; no fixed sensor transform is fitted.
    relative = [images[2 * i + 2]["R"] @ images[2 * i + 1]["R"].T for i in range(5)]
    assert np.linalg.norm(relative[0] - relative[-1]) > 0.01


def test_preparation_uses_all_pairs_without_score_filtering(tmp_path):
    import csv

    from compare import prepare
    from PIL import Image

    source = tmp_path / "BoobyPass"
    source.mkdir()
    for side, size in [("A", (5312, 2988)), ("B", (3840, 2160))]:
        (source / side).mkdir()
        for i in range(3):
            Image.new("RGB", size, (60, 100, 140)).save(source / side / f"pair_{i}.jpg")
    with (source / "pairs.csv").open("w") as f:
        w = csv.writer(f)
        w.writerow(["pair", "file", "sync_residual_ms"])
        for i in range(3):
            w.writerow([i, f"pair_{i}.jpg", 1.8])
    before = set(source.rglob("*"))
    output = tmp_path / "output"
    m = prepare(source, output)
    assert m["selectedPairs"] == 3 and m["qualityThreshold"] is None and not m["fixedRig"]
    assert m["initialFocalPixels"]["A"] == pytest.approx(4218.72042075)
    assert set(source.rglob("*")) == before
    assert all(p.is_symlink() for p in (output / "images").rglob("*.jpg"))
    with pytest.raises(ValueError, match="fresh output"):
        prepare(source, output)


def test_worker_rejects_volume_path_escape(tmp_path, monkeypatch):
    from serverless import source_path

    monkeypatch.setenv("OPENREEF_VOLUME_ROOT", str(tmp_path))
    for value in ["../outside", "/etc", "site/../../../etc"]:
        with pytest.raises(ValueError):
            source_path(value)


def test_text_models_load_in_native_colmap(tmp_path):
    import shutil
    import subprocess

    binary = shutil.which("colmap")
    if not binary:
        pytest.skip("Native COLMAP unavailable")
    cameras, images, points = scene()
    model = tmp_path / "text"
    write_model(model, cameras, images, points)
    target = tmp_path / "binary"
    target.mkdir()
    subprocess.run(
        [
            binary,
            "model_converter",
            "--input_path",
            str(model),
            "--output_path",
            str(target),
            "--output_type",
            "BIN",
        ],
        check=True,
    )
    assert (target / "images.bin").stat().st_size > 100


def test_products_survive_scratch_removal(tmp_path, monkeypatch):
    import shutil

    from compare import persist_products, product_scratch

    root = tmp_path / "volume/run"
    (root / "colmap").mkdir(parents=True)
    monkeypatch.setenv("PAIRED_PRODUCT_SCRATCH", str(tmp_path / "compute"))
    scratch = product_scratch(root)
    for name, content in [
        ("model.glb", b"glb-test"),
        ("model_orthomap.png", b"ortho-test"),
        ("model_orthomap.txt", b"coordinates"),
        ("scene_mesh.ply", b"mesh"),
    ]:
        (root / "openmvs" / name).write_bytes(content)
    (root / "openmvs/scene.mvs").write_bytes(b"ephemeral references")
    persist_products(root, scratch)
    shutil.rmtree(scratch)
    assert not (root / "openmvs").is_symlink()
    assert (root / "openmvs/model.glb").read_bytes() == b"glb-test"
    assert (root / "openmvs/model_orthomap.png").read_bytes() == b"ortho-test"
    assert not (root / "openmvs/scene.mvs").exists()
    assert not (root / "colmap/dense").exists()
