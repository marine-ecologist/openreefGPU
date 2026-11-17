"""Joint photogrammetry with estimated camera intrinsics and unconstrained poses."""

from __future__ import annotations

import json
import sqlite3
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


def run(dataset: Path) -> None:
    config = dataset / "joint"
    settings = json.loads((config / "settings.json").read_text())
    database = dataset / "colmap/database.db"
    database.parent.mkdir(exist_ok=True)

    def execute(command):
        print("Command: " + json.dumps(command), flush=True)
        subprocess.run(command, check=True)

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
    execute(
        settings["pipelineCommand"]
        + [
            "--no-single-camera",
            "--camera-model",
            "OPENCV",
            "--stages",
            "sparse,markertags,undistort,openmvs_import,dense,mesh,texture",
        ]
    )

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
    diagnostic["sparseModels"] = models
    (config / "diagnostics.json").write_text(json.dumps(diagnostic, indent=2) + "\n")
    if not models or set(
        max(models, key=lambda m: m["registeredImages"])["registeredBySource"]
    ) != {"camera-A", "camera-B"}:
        raise RuntimeError("The largest sparse reconstruction does not include both cameras")


if __name__ == "__main__":
    run(Path(sys.argv[1]))
