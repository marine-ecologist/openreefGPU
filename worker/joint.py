"""Joint photogrammetry with estimated camera intrinsics and unconstrained poses."""

from __future__ import annotations

import json
import shutil
import sqlite3
import struct
import subprocess
import sys
from pathlib import Path


def candidate_pairs(names: list[str], overlap: int = 10) -> list[tuple[str, str]]:
    """Use corresponding frame names as candidates, never as rigid stereo constraints."""
    from openreef.pipeline.stereo import stereo_match_pairs

    groups = {}
    for name in sorted(names):
        groups.setdefault(Path(name).stem, []).append({"image": name})
    frames = [{"images": items} for _, items in sorted(groups.items())]
    return stereo_match_pairs(frames, overlap, 50, 5)


def verify_cameras(database: Path) -> dict:
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as conn:
        rows = list(conn.execute("SELECT name, camera_id FROM images"))
        grouped = {}
        for name, identity in rows:
            grouped.setdefault(name.split("/")[0], set()).add(identity)
        if set(grouped) != {"camera-A", "camera-B"}:
            raise RuntimeError("Joint extraction did not preserve both camera groups")
        if any(len(ids) != 1 for ids in grouped.values()):
            raise RuntimeError("Expected one estimated camera model per source folder")
        if grouped["camera-A"] == grouped["camera-B"]:
            raise RuntimeError("The two source cameras were incorrectly merged")
        return {
            "imageCount": len(rows),
            "cameras": {key: next(iter(value)) for key, value in grouped.items()},
            "fixedRig": False,
            "metricCalibration": False,
        }


def copy_database(source: Path, destination: Path) -> None:
    """Take a consistent SQLite snapshot without changing the retained source."""
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src:
        with sqlite3.connect(destination) as dst:
            src.backup(dst)


def largest_snapshot(root: Path) -> Path | None:
    candidates = []
    for binary in root.glob("*/images.bin"):
        if all((binary.parent / name).is_file() for name in ("cameras.bin", "points3D.bin")):
            with binary.open("rb") as stream:
                count = struct.unpack("<Q", stream.read(8))[0]
            candidates.append((count, str(binary.parent), binary.parent))
    return max(candidates)[2] if candidates else None


def mapper_command(dataset: Path, cores: int, initial: Path | None = None) -> list[str]:
    command = [
        "colmap",
        "mapper",
        "--database_path",
        str(dataset / "colmap/database.db"),
        "--image_path",
        str(dataset / "images"),
        "--output_path",
        str(dataset / "colmap/sparse"),
        "--Mapper.num_threads",
        str(cores),
        "--Mapper.ba_use_gpu",
        "1",
        "--Mapper.ba_gpu_index",
        "-1",
        "--Mapper.ba_global_frames_ratio",
        "1.5",
        "--Mapper.ba_global_points_ratio",
        "1.5",
        "--Mapper.ba_global_frames_freq",
        "1000",
        "--Mapper.ba_global_points_freq",
        "1000000",
        "--Mapper.snapshot_path",
        str(dataset / "joint/snapshots"),
        "--Mapper.snapshot_frames_freq",
        "100",
    ]
    if initial:
        command += ["--input_path", str(initial)]
    return command


def run(dataset: Path) -> None:
    config = dataset / "joint"
    settings = json.loads((config / "settings.json").read_text())
    database = dataset / "colmap/database.db"
    database.parent.mkdir(exist_ok=True)

    def execute(command):
        print("Command: " + json.dumps(command), flush=True)
        subprocess.run(command, check=True)

    reused = settings.get("reuseDatabase")
    if reused:
        copy_database(Path(reused), database)
        print(
            "Reusing verified features and matches from " + settings["resumeSourceRun"], flush=True
        )
    else:
        execute(
            [
                "colmap",
                "feature_extractor",
                "--database_path",
                str(database),
                "--image_path",
                str(dataset / "images"),
                "--ImageReader.camera_model",
                "OPENCV",
                "--ImageReader.single_camera",
                "0",
                "--ImageReader.single_camera_per_folder",
                "1",
                "--FeatureExtraction.num_threads",
                str(settings["cores"]),
                "--FeatureExtraction.max_image_size",
                str(settings["maxImageSize"]),
                "--FeatureExtraction.use_gpu",
                "1",
            ]
        )
    diagnostic = verify_cameras(database)
    if diagnostic["imageCount"] != len(settings["images"]):
        raise RuntimeError("Not all source images were extracted")
    pairs = candidate_pairs(settings["images"], settings["overlap"])
    pair_file = config / "pairs.txt"
    pair_file.write_text("".join(f"{a} {b}\n" for a, b in pairs))
    diagnostic["candidatePairs"] = len(pairs)
    (config / "diagnostics.json").write_text(json.dumps(diagnostic, indent=2) + "\n")
    if not reused:
        execute(
            [
                "colmap",
                "matches_importer",
                "--database_path",
                str(database),
                "--match_list_path",
                str(pair_file),
                "--match_type",
                "pairs",
                "--FeatureMatching.use_gpu",
                "1",
                "--FeatureMatching.num_threads",
                str(settings["cores"]),
            ]
        )
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as conn:
        diagnostic["verifiedPairs"] = conn.execute(
            "SELECT COUNT(*) FROM two_view_geometries WHERE rows > 0"
        ).fetchone()[0]
    (config / "diagnostics.json").write_text(json.dumps(diagnostic, indent=2) + "\n")
    if not diagnostic["verifiedPairs"]:
        raise RuntimeError("Joint matching produced no verified image pairs")
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as conn:
        ids = dict(conn.execute("SELECT image_id, name FROM images"))
        edges = conn.execute("SELECT pair_id FROM two_view_geometries WHERE rows > 0")
        diagnostic["verifiedCrossCameraPairs"] = sum(
            ids[pair // 2147483647].split("/")[0] != ids[pair % 2147483647].split("/")[0]
            for (pair,) in edges
        )
    (config / "diagnostics.json").write_text(json.dumps(diagnostic, indent=2) + "\n")
    if not diagnostic["verifiedCrossCameraPairs"]:
        raise RuntimeError("No verified matches connect the two cameras")
    sparse = dataset / "colmap/sparse"
    sparse.mkdir()
    if settings.get("reuseSparseModels"):
        previous = Path(settings["reuseSparseModels"])
        for model in sorted(previous.iterdir()):
            if model.is_symlink() or not model.is_dir():
                continue
            required = ("cameras.bin", "images.bin", "points3D.bin")
            if all((model / part).is_file() for part in required):
                target = sparse / model.name
                target.mkdir()
                for part in model.glob("*.bin"):
                    if part.is_symlink() or not part.resolve().is_relative_to(previous.resolve()):
                        raise ValueError("Sparse model link escapes its retained run")
                    shutil.copy2(part, target / part.name)
    else:
        (config / "snapshots").mkdir()
        initial = None
        if settings.get("resumeSnapshots"):
            snapshot = largest_snapshot(Path(settings["resumeSnapshots"]))
            if snapshot:
                initial = config / "initial-model"
                shutil.copytree(snapshot, initial)
        execute(mapper_command(dataset, settings["cores"], initial))

    from openreef.io.markertags import read_registered_images

    models = []
    for binary in sorted((dataset / "colmap/sparse").glob("*/images.bin")):
        registered = read_registered_images(binary)
        by_source = {}
        for name in registered:
            source = name.split("/")[0]
            by_source[source] = by_source.get(source, 0) + 1
        models.append(
            {
                "path": str(binary.parent),
                "registeredImages": len(registered),
                "registeredBySource": by_source,
            }
        )
    from worker.merge import image_names, merge_components

    all_names = [image_names(Path(item["path"])) for item in models]
    diagnostic["uniqueRegisteredImages"] = len(set.union(*all_names)) if all_names else 0
    diagnostic["unregisteredImages"] = sorted(set(settings["images"]) - set().union(*all_names))
    diagnostic["modelImageNames"] = {
        Path(item["path"]).name: sorted(names)
        for item, names in zip(models, all_names, strict=True)
    }
    if settings.get("reuseSparseModels"):
        merged, report = merge_components(
            [Path(item["path"]) for item in models], config / "merge-attempts"
        )
        diagnostic["merge"] = report
        (config / "diagnostics.json").write_text(json.dumps(diagnostic, indent=2) + "\n")
        if not report["improvedLargest"]:
            print(
                "No geometrically validated merge improved the retained model; no dense rerun",
                flush=True,
            )
            return
        target = sparse / "merged"
        shutil.copytree(merged, target)
        execute(
            [
                "colmap",
                "bundle_adjuster",
                "--input_path",
                str(target),
                "--output_path",
                str(target),
                "--BundleAdjustmentCeres.use_gpu",
                "1",
                "--BundleAdjustmentCeres.gpu_index",
                "-1",
            ]
        )
        registered = image_names(target)
        models.append(
            {
                "path": str(target),
                "registeredImages": len(registered),
                "registeredBySource": {
                    cam: sum(name.startswith(cam + "/") for name in registered)
                    for cam in ("camera-A", "camera-B")
                },
            }
        )
    diagnostic["sparseModels"] = models
    (config / "diagnostics.json").write_text(json.dumps(diagnostic, indent=2) + "\n")
    if not models or set(
        max(models, key=lambda m: m["registeredImages"])["registeredBySource"]
    ) != {"camera-A", "camera-B"}:
        raise RuntimeError("The largest sparse reconstruction does not include both cameras")

    from openreef.pipeline.stages import sparse_model_score
    from openreef.pipeline.tasks import _create_point_profiles

    complete = [Path(item["path"]) for item in models]
    for model in complete:
        cloud = model / "points3D.ply"
        execute(
            [
                "colmap",
                "model_converter",
                "--input_path",
                str(model),
                "--output_path",
                str(cloud),
                "--output_type",
                "PLY",
            ]
        )
        _create_point_profiles(cloud, "compact", 20, 5, 1)
    selected = max(complete, key=sparse_model_score)
    (sparse / "selected").symlink_to(selected.name, target_is_directory=True)
    execute(
        settings["pipelineCommand"]
        + [
            "--no-single-camera",
            "--camera-model",
            "OPENCV",
            "--stages",
            "markertags,undistort,openmvs_import,dense,mesh,texture",
        ]
    )


if __name__ == "__main__":
    run(Path(sys.argv[1]))
