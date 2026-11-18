#!/usr/bin/env python3
"""Isolated native comparison. Preparation is read-only on source images/projects."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
import subprocess
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from geometry import (
    baseline_report,
    orient,
    prune,
    rescale,
    scores,
    solve,
    write_model,
)
from PIL import Image

DIFFERENCES = [
    (
        "Generic selection is COLMAP vocabulary-tree retrieval (100 candidates), not "
        "Metashape generic selection."
    ),
    (
        "40k SIFT features; 10k verified matches per pair and at most 10k selected "
        "observations per image are native approximations of the Metashape limits."
    ),
    (
        "Reconstruction uncertainty uses ray-information anisotropy, not the proprietary "
        "Metashape uncertainty score."
    ),
    (
        "Reprojection cleanup uses raw full-resolution pixel RMS, not a claim of equivalent "
        "Metashape weighted error."
    ),
    (
        "Native Huber reprojection loss; per-pair distances use Gaussian 0.003 m accuracy "
        "without fixing relative rotation."
    ),
    "No rolling-shutter solver; this assumes the supplied stabilized frames are suitable.",
    (
        "OpenMVS dense reconstruction/GLB/orthographic PNG are available after QC; "
        "Metashape Mild filtering, .tls/Cesium LOD, DEM and refined BigTIFF mosaic are not "
        "reproduced by this check."
    ),
    (
        "Scale is constrained by the supplied physical baseline, not independently "
        "validated ground truth."
    ),
]


def save(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2) + "\n")
    tmp.replace(path)


def digest(path):
    h = hashlib.md5()
    with Path(path).open("rb") as f:
        for part in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def quality_from_project(project, source):
    """Only reuse image-quality metadata; never read camera transforms as inputs."""
    files = Path(project).with_suffix(".files")
    with zipfile.ZipFile(files / "project.zip") as z:
        doc = ET.fromstring(z.read("doc.xml"))
    active = doc.find("chunks").get("active_id")
    chunk = next(c for c in doc.findall("./chunks/chunk") if c.get("id") == active)
    chunkpath = files / chunk.get("path")
    with zipfile.ZipFile(chunkpath) as z:
        xml = ET.fromstring(z.read("doc.xml"))
    frames = xml.findall("./frames/frame")
    if len(frames) != 1:
        raise ValueError("Expected one active frame for quality metadata")
    framepath = chunkpath.parent / frames[0].get("path")
    with zipfile.ZipFile(framepath) as z:
        frame = z.read("doc.xml")
    quality = {}
    hashes = {}
    for camera in ET.fromstring(frame).findall("./cameras/camera"):
        photo = camera.find("photo")
        entry = camera.find('./meta/property[@name="Image/Quality"]')
        if photo is None or entry is None:
            continue
        old = (framepath.parent / photo.get("path")).resolve()
        side = old.parent.name
        name = old.name
        if side not in ("A", "B") or old.parent.parent.name != source.name:
            continue
        local = source / side / name
        if not old.is_file() or not local.is_file():
            raise ValueError("Quality source image is unavailable: " + name)
        localhash = digest(local)
        if localhash != digest(old):
            raise ValueError("Metashape quality belongs to different image bytes: " + name)
        quality.setdefault(name, {})[side] = float(entry.get("value"))
        hashes[f"{side}/{name}"] = localhash
    return (
        quality,
        hashes,
        {
            "project": str(Path(project).resolve()),
            "frameXmlSha256": hashlib.sha256(frame).hexdigest(),
            "qualityOnly": True,
        },
    )


def prepare(source, output, quality_project=None, pair_limit=None):
    source = Path(source).resolve()
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Use a fresh output directory; existing results are never overwritten")
    rows = list(csv.DictReader((source / "pairs.csv").open(newline="")))
    if pair_limit is not None:
        rows = rows[:pair_limit]
    hashes = {}
    if quality_project:
        quality, hashes, provenance = quality_from_project(quality_project, source)
    elif (source / "quality.csv").exists():
        quality = {
            r["file"]: {"A": float(r["quality_A"]), "B": float(r["quality_B"])}
            for r in csv.DictReader((source / "quality.csv").open())
        }
        provenance = {"csv": str(source / "quality.csv"), "qualityOnly": True}
    else:
        quality = None
        provenance = {"selection": "all supplied pairs; no score-based filtering"}
    expected_checksums = {}
    if (source / "input-checksums.json").exists():
        expected_checksums = {
            v["path"]: v for v in json.loads((source / "input-checksums.json").read_text())
        }
    selected = []
    dimensions = {}
    files = []
    for row in rows:
        name = row["file"]
        if Path(name).name != name or "/" in name or "\\" in name:
            raise ValueError("Invalid pair filename")
        q = quality.get(name) if quality is not None else None
        if quality is not None and (not q or set(q) != set("AB")):
            raise ValueError("Missing per-camera quality: " + name)
        if q is not None and min(q.values()) < 0.4:
            continue
        for side, expected in [("A", (5312, 2988)), ("B", (3840, 2160))]:
            path = source / side / name
            if not path.resolve().is_relative_to(source):
                raise ValueError("Image escapes source folder")
            with Image.open(path) as im:
                size = im.size
            if size != expected:
                raise ValueError(f"Unexpected video mode: {side}/{name} {size}")
            dimensions[side] = size
            key = f"{side}/{name}"
            files.append(
                {"path": key, "bytes": path.stat().st_size, "md5": hashes.get(key) or digest(path)}
            )
        selected.append(row)
    for record in files:
        if expected_checksums and record != expected_checksums.get(record["path"]):
            raise ValueError("Uploaded input checksum mismatch: " + record["path"])
    if len(selected) < 3:
        raise ValueError("Fewer than three good pairs")
    output.mkdir(parents=True)
    images = output / "images"
    images.mkdir()
    for side in "AB":
        target = images / f"camera-{side}"
        target.mkdir()
        for row in selected:
            (target / row["file"]).symlink_to(source / side / row["file"])
    half = math.asin(math.sin(math.radians(123.6) / 2) / 1.333)
    focal = {side: math.hypot(*size) / 2 / half for side, size in dimensions.items()}
    if quality is not None:
        with (output / "quality.csv").open("w") as f:
            writer = csv.writer(f)
            writer.writerow(["file", "quality_A", "quality_B"])
            for row in rows:
                writer.writerow([row["file"], quality[row["file"]]["A"], quality[row["file"]]["B"]])
    shutil.copy2(source / "pairs.csv", output / "pairs.csv")
    manifest = {
        "source": str(source),
        "site": source.name,
        "inputPairs": len(rows),
        "selectedPairs": len(selected),
        "selectedImages": 2 * len(selected),
        "qualityThreshold": 0.4 if quality is not None else None,
        "stationStep": 1,
        "qualityProvenance": provenance,
        "pairs": selected,
        "files": files,
        "initialFocalPixels": focal,
        "baselineMetres": 0.351,
        "baselineSigmaMetres": 0.003,
        "baselineSpreadLimit": 0.025,
        "outlierFractionLimit": 0.05,
        "fixedRig": False,
        "rollingShutterCorrection": False,
        "fullResolution": True,
        "fit": ["f", "cx", "cy", "k1", "k2", "k3", "p1", "p2"],
        "differences": DIFFERENCES,
        "scope": "temporary native geometry comparison; products require QC and explicit request",
    }
    save(output / "experiment.json", manifest)
    return manifest


def cap_points(images, points, limit=10000):
    tracks = {key: set() for key in points}
    for key, im in images.items():
        for pid in im["obs"][:, 2]:
            if int(pid) in tracks:
                tracks[int(pid)].add(key)
    counts = {key: 0 for key in images}
    keep = set()
    for pid in sorted(points, key=lambda p: len(tracks[p]), reverse=True):
        track = tracks[pid]
        if len(track) < 2 or any(counts[key] >= limit for key in track):
            continue
        keep.add(pid)
        for key in track:
            counts[key] += 1
    for pid in list(points):
        if pid not in keep:
            del points[pid]
    if min(counts.values()) < 6:
        raise ValueError(
            "At least one registered image has fewer than six retained observations; review mapping"
        )
    return {"pointsRetained": len(points), "maximumObservationsPerImage": max(counts.values())}


def run(args):
    root = args.output.resolve()
    manifest = json.loads((root / "experiment.json").read_text())
    if args.prepared:
        for record in manifest["files"]:
            path = Path(manifest["source"]) / record["path"]
            if path.stat().st_size != record["bytes"] or digest(path) != record["md5"]:
                raise ValueError("Prepared source files changed")
    state = {
        "state": "running",
        "phase": "features",
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "inputImages": manifest["selectedImages"],
    }
    save(root / "run.json", state)

    def execute(command):
        print("Command: " + json.dumps(list(map(str, command))), flush=True)
        subprocess.run(list(map(str, command)), check=True)

    try:
        db = root / "colmap/database.db"
        db.parent.mkdir(exist_ok=True)
        raw = root / "colmap/raw"
        raw.mkdir()
        for side in "AB":
            image_list = root / f"{side}-images.txt"
            image_list.write_text(
                "".join(f"camera-{side}/{row['file']}\n" for row in manifest["pairs"])
            )
            w, h = (5312, 2988) if side == "A" else (3840, 2160)
            f = manifest["initialFocalPixels"][side]
            execute(
                [
                    args.colmap,
                    "feature_extractor",
                    "--database_path",
                    db,
                    "--image_path",
                    root / "images",
                    "--image_list_path",
                    image_list,
                    "--ImageReader.single_camera_per_folder",
                    "1",
                    "--ImageReader.camera_model",
                    "THIN_PRISM_FISHEYE",
                    "--ImageReader.camera_params",
                    f"{f},{f},{w / 2},{h / 2},0,0,0,0,0,0,0,0",
                    "--FeatureExtraction.max_image_size",
                    "-1",
                    "--SiftExtraction.max_num_features",
                    "40000",
                    "--FeatureExtraction.num_threads",
                    args.cores,
                    "--FeatureExtraction.use_gpu",
                    int(not args.cpu),
                ]
            )
        import sqlite3

        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as connection:
            groups = {}
            for name, camera_id in connection.execute("SELECT name, camera_id FROM images"):
                groups.setdefault(name.split("/")[0], set()).add(camera_id)
        if (
            set(groups) != {"camera-A", "camera-B"}
            or any(len(v) != 1 for v in groups.values())
            or groups["camera-A"] == groups["camera-B"]
        ):
            raise ValueError("Feature extraction did not preserve exactly two independent sensors")
        state["phase"] = "generic_matching"
        save(root / "run.json", state)
        tree = root / "vocab_tree.bin"
        if args.vocab_tree:
            shutil.copy2(args.vocab_tree, tree)
        else:
            url = "https://github.com/colmap/colmap/releases/download/3.11.1/vocab_tree_faiss_flickr100K_words256K.bin"
            urllib.request.urlretrieve(url, tree)
            sha = hashlib.sha256(tree.read_bytes()).hexdigest()
            if sha != "96ca8ec8ea60b1f73465aaf2c401fd3b3ca75cdba2d3c50d6a2f6f760f275ddc":
                raise ValueError("Vocabulary-tree checksum mismatch")
        execute(
            [
                args.colmap,
                "vocab_tree_matcher",
                "--database_path",
                db,
                "--VocabTreeMatching.vocab_tree_path",
                tree,
                "--VocabTreeMatching.num_images",
                "100",
                "--VocabTreeMatching.max_num_features",
                "10000",
                "--FeatureMatching.num_threads",
                args.cores,
                "--FeatureMatching.use_gpu",
                int(not args.cpu),
                "--FeatureMatching.max_num_matches",
                "10000",
                "--TwoViewGeometry.filter_stationary_matches",
                "1",
            ]
        )
        state["phase"] = "mapping"
        save(root / "run.json", state)
        snapshots = root / "snapshots"
        snapshots.mkdir()
        execute(
            [
                args.colmap,
                "mapper",
                "--database_path",
                db,
                "--image_path",
                root / "images",
                "--output_path",
                raw,
                "--Mapper.num_threads",
                args.cores,
                "--Mapper.ba_refine_principal_point",
                "1",
                "--Mapper.ba_use_gpu",
                int(not args.cpu),
                "--Mapper.ba_global_frames_ratio",
                "1.5",
                "--Mapper.ba_global_points_ratio",
                "1.5",
                "--Mapper.snapshot_path",
                snapshots,
                "--Mapper.snapshot_frames_freq",
                "100",
            ]
        )
        models = []
        from geometry import read_model

        for model in sorted(raw.iterdir()):
            if not (model / "images.bin").exists():
                continue
            text = root / ("model-" + model.name)
            text.mkdir()
            execute(
                [
                    args.colmap,
                    "model_converter",
                    "--input_path",
                    model,
                    "--output_path",
                    text,
                    "--output_type",
                    "TXT",
                ]
            )
            cameras, images, points = read_model(text)
            models.append((len(images), text, cameras, images, points))
        if not models:
            raise ValueError("No sparse reconstruction")
        _, text, cameras, images, points = max(models, key=lambda m: m[0])
        coverage = len(images) / manifest["selectedImages"]
        state.update(
            phase="distance_constrained_bundle_adjustment",
            components=[{"path": str(m[1]), "images": m[0]} for m in models],
            largestCoverage=coverage,
        )
        save(root / "run.json", state)
        report = {
            "manifest": "experiment.json",
            "differences": DIFFERENCES,
            "components": state["components"],
            "largestCoverage": coverage,
            "pointCap": cap_points(images, points),
        }
        pre = baseline_report(images)
        rescale(images, points, 0.351 / pre["medianMetres"])
        report["beforeDistanceConstraints"] = baseline_report(images)
        report["initialFit"] = solve(
            args.solver, root / "fit-0", cameras, images, points, bars=False, gpu=not args.cpu
        )
        ids, reprojection, uncertainty = scores(cameras, images, points)
        report["uncertaintyRemoved"] = prune(points, ids, uncertainty, 15, 0.20)
        report["uncertaintyFit"] = solve(
            args.solver, root / "fit-1", cameras, images, points, bars=False, gpu=not args.cpu
        )
        ids, reprojection, _ = scores(cameras, images, points)
        report["reprojectionRemoved"] = prune(points, ids, reprojection, 0.3, 0.10)
        report["cleanupFit"] = solve(
            args.solver, root / "fit-2", cameras, images, points, bars=False, gpu=not args.cpu
        )
        report["distanceFit"] = solve(
            args.solver, root / "fit-bars", cameras, images, points, bars=True, gpu=not args.cpu
        )
        report["afterDistanceConstraints"] = baseline_report(images)
        qc = report["afterDistanceConstraints"]
        report["baselineQCPassed"] = (
            qc["medianAbsoluteRelativeDeviation"] <= 0.025 and qc["fractionOver10Percent"] <= 0.05
        )
        save(root / "comparison.json", report)
        # Always retain fitted geometry even if QC fails, but never silently produce an ortho
        # from it.
        fitted = root / "fitted-text"
        write_model(fitted, cameras, images, points)
        if not report["baselineQCPassed"]:
            raise ValueError("Baseline QC failed; fitted geometry retained, products stopped")
        outliers = set(qc["outlierImages"])
        images = {key: im for key, im in images.items() if im["name"] not in outliers}
        tracks = {pid: 0 for pid in points}
        for im in images.values():
            for pid in im["obs"][:, 2]:
                if int(pid) in tracks:
                    tracks[int(pid)] += 1
        points = {pid: p for pid, p in points.items() if tracks[pid] >= 2}
        rescale(images, points, 0.351 / baseline_report(images)["medianMetres"])
        report["finalBaselines"] = baseline_report(images)
        report["orientation"] = orient(images, points)
        report["fittedCameraParameters"] = {str(key): c["p"].tolist() for key, c in cameras.items()}
        save(root / "comparison.json", report)
        oriented = root / "oriented-text"
        write_model(oriented, cameras, images, points)
        checked = root / "colmap/sparse/checked"
        checked.mkdir(parents=True)
        execute(
            [
                args.colmap,
                "model_converter",
                "--input_path",
                oriented,
                "--output_path",
                checked,
                "--output_type",
                "BIN",
            ]
        )
        execute(
            [
                args.colmap,
                "model_converter",
                "--input_path",
                checked,
                "--output_path",
                checked / "points3D.ply",
                "--output_type",
                "PLY",
            ]
        )
        (checked.parent / "selected").symlink_to("checked", target_is_directory=True)
        if args.products:
            if coverage < 0.95:
                raise ValueError(
                    "Largest component covers less than 95% of eligible images; products stopped"
                )
            execute(
                [
                    "openreef-pipeline",
                    root,
                    "--stages",
                    "undistort,openmvs_import,dense,mesh",
                    "--no-single-camera",
                    "--camera-model",
                    "THIN_PRISM_FISHEYE",
                    "--max-image-size",
                    "5312",
                    "--resolution-level",
                    "1",
                    "--max-resolution",
                    "10000",
                    "--dense-original",
                    "--cores",
                    args.cores,
                    "--max-texture-size",
                    "8192",
                ]
            )
            # Native mesh-based orthographic rendering, explicitly different from a DEM-based
            # mosaic.
            execute(
                [
                    "TextureMesh",
                    root / "openmvs/scene_mesh.mvs",
                    "--output-file",
                    root / "openmvs/paired_check_texture.mvs",
                    "--export-type",
                    "glb",
                    "--mesh-file",
                    root / "openmvs/scene_mesh.ply",
                    "--resolution-level",
                    "0",
                    "--max-texture-size",
                    "8192",
                    "--orthographic-image-resolution",
                    "8192",
                    "--max-threads",
                    args.cores,
                ]
            )
        state.update(
            state="completed",
            phase="products" if args.products else "oriented_sparse_check",
            report=str(root / "comparison.json"),
            completedAt=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        save(root / "run.json", state)
    except Exception as error:
        state.update(state="failed", error=str(error))
        save(root / "run.json", state)
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--quality-project", type=Path)
    p.add_argument("--pair-limit", type=int)
    p.add_argument("--run", action="store_true")
    p.add_argument("--prepared", action="store_true")
    p.add_argument("--products", action="store_true")
    p.add_argument("--cpu", action="store_true")
    p.add_argument("--cores", type=int, default=16)
    p.add_argument("--colmap", default="colmap")
    p.add_argument(
        "--solver", type=Path, default=Path(__file__).parent / "build/paired-baseline-ba"
    )
    p.add_argument("--vocab-tree", type=Path)
    args = p.parse_args()
    if args.pair_limit is not None and args.pair_limit < 3:
        p.error("--pair-limit must be at least 3")
    if not 1 <= args.cores <= 256:
        p.error("--cores must be between 1 and 256")
    if not args.prepared:
        m = prepare(args.source, args.output, args.quality_project, args.pair_limit)
        print(
            json.dumps(
                {
                    k: m[k]
                    for k in ("site", "inputPairs", "selectedPairs", "initialFocalPixels", "scope")
                },
                indent=2,
            )
        )
    if args.run:
        run(args)


if __name__ == "__main__":
    main()
