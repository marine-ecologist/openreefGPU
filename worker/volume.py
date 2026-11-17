"""Drive-free serverless runs against a mounted network volume."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}


def confined(root: Path, value: str) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ValueError("Volume paths must be nonempty relative paths")
    path = (root / value).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("Volume path escapes its permitted root")
    return path


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def run(payload: dict, worker) -> dict:
    import openreef

    versions = worker.validate_version_contract(payload, openreef.__version__)
    if payload.get("schema_version") != worker.JOB_SCHEMA_VERSION:
        raise ValueError("Unsupported job schema version")
    mount = Path(os.environ.get("OPENREEF_VOLUME_ROOT", "/runpod-volume")).resolve()
    if not mount.is_dir():
        raise ValueError("Network volume is not mounted")
    sources = confined(mount, "openreef-sources")
    request = payload["volume"]
    if not isinstance(request, dict):
        raise ValueError("volume must be an object")
    folder = confined(sources, request.get("folder"))
    if not folder.is_dir():
        raise ValueError("Source folder does not exist on the volume")
    files = sorted(
        p for p in folder.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES and p.is_file()
    )
    if len(files) < 2:
        raise ValueError("Source folder requires at least two photographs")
    for path in files:
        if not path.resolve().is_relative_to(sources):
            raise ValueError("Source image link escapes the source root")
    mode = request.get("mode", "stereo" if "right_folder" in request else "single")
    if mode not in {"single", "stereo", "joint"}:
        raise ValueError("Unknown volume reconstruction mode")
    joint = mode == "joint"
    stereo = mode == "stereo"
    if joint and (request.get("profile") or request.get("timestamps")):
        raise ValueError("Joint estimation does not use a fixed rig profile or stereo timestamps")
    start, count = request.get("start", 0), request.get("count")
    if (
        type(start) is not int
        or start < 0
        or (count is not None and (type(count) is not int or count < 2))
    ):
        raise ValueError("start must be nonnegative and count must be at least two")
    if (stereo or joint) and (start or count is not None):
        raise ValueError("Stereo subsets require preselected, timestamp-aligned folders")
    files = files[start:] if count is None else files[start : start + count]
    if len(files) < 2 or (count is not None and len(files) != count):
        raise ValueError("Not enough source photographs for the requested subset")
    right = None
    profile = timestamps = None
    if stereo or joint:
        right = confined(sources, request["right_folder"])
        if not right.is_dir() or right == folder:
            raise ValueError("Stereo requires two distinct source folders")
        for path in right.iterdir():
            if path.is_file() and not path.resolve().is_relative_to(sources):
                raise ValueError("Source image link escapes the source root")
    if stereo:
        profile = json.loads(confined(sources, request.get("profile")).read_text())
        if request.get("timestamps"):
            timestamps = json.loads(confined(sources, request["timestamps"]).read_text())
        from openreef.pipeline.stereo import validate_profile

        validate_profile(profile)
    options = payload.get("pipeline", {})
    if not isinstance(options, dict):
        raise ValueError("pipeline must be an object")
    allowed = {
        "max_image_size": ("--max-image-size", 256, 16384),
        "max_resolution": ("--max-resolution", 256, 16384),
        "cores": ("--cores", 1, 256),
        "sequential_overlap": ("--sequential-overlap", 1, 1000),
    }
    if set(options) - set(allowed) - {"stages"}:
        raise ValueError("Unknown pipeline option")
    extra = []
    for key, (flag, low, high) in allowed.items():
        if key in options:
            value = options[key]
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"Invalid {key}")
            extra.extend([flag, str(value)])
    if "stages" in options:
        from openreef.pipeline.cli import _stage_list

        stages = options["stages"]
        if not isinstance(stages, str):
            raise ValueError("stages must be a comma-separated string")
        _stage_list(stages)
        extra.extend(["--stages", stages])
    if joint and "stages" in options:
        raise ValueError("Joint mode currently runs the full reconstruction pipeline")
    # Every invocation, including a RunPod retry, gets a fresh persistent workspace.
    runs = confined(mount, "openreef-runs")
    runs.mkdir(exist_ok=True)
    run_id = str(uuid.uuid4())
    workspace = runs / run_id
    workspace.mkdir()
    dataset = workspace / "dataset"
    dataset.mkdir()
    # Persist metadata before preparation too, so calibration errors are retained.
    images = dataset / "images"
    if not stereo and not joint:
        images.mkdir()
        for path in files:
            (images / path.name).symlink_to(path.resolve())
    command = worker.pipeline_command(dataset) + extra + (["--stereo"] if stereo else [])
    if joint:
        right_files = sorted(
            p for p in right.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
        )
        if len(right_files) < 2:
            raise ValueError("The second source folder requires at least two photographs")
        images.mkdir()
        for camera, selected in [("camera-A", files), ("camera-B", right_files)]:
            target = images / camera
            target.mkdir()
            for path in selected:
                (target / path.name).symlink_to(path.resolve())
        config = dataset / "joint"
        config.mkdir()
        atomic_json(
            config / "settings.json",
            {
                "images": [
                    str(p.relative_to(images)) for p in sorted(images.rglob("*")) if p.is_file()
                ],
                "cores": options.get("cores", worker.pipeline_core_count()),
                "maxImageSize": options.get("max_image_size", 1600),
                "overlap": options.get("sequential_overlap", 10),
                "pipelineCommand": command,
            },
        )
        command = [sys.executable, "-u", "-m", "worker.joint", str(dataset)]
    state = {
        "id": run_id,
        "state": "running",
        "source": str(folder),
        "imageCount": len(files) + (len(right_files) if joint else 0),
        "mode": mode,
        "versions": versions,
        "dataset": str(dataset),
        "command": command,
        "startedAt": worker.utc_now(),
        "filesDownloaded": 0,
    }
    if joint:
        state["rightSource"] = str(right)
        state["metricCalibration"] = False
    status = workspace / "job.json"
    atomic_json(status, state)
    print(json.dumps({"volumeRun": str(workspace), "statusPath": str(status)}), flush=True)
    atomic_json(
        workspace / "source-manifest.json",
        {
            "folder": str(folder),
            "files": [
                {"name": p.name, "size": p.stat().st_size, "mtimeNs": p.stat().st_mtime_ns}
                for p in files
            ],
        },
    )
    if joint:
        atomic_json(
            workspace / "source-manifest.json",
            {
                "folders": [str(folder), str(right)],
                "files": [
                    {
                        "image": str(p.relative_to(images)),
                        "source": str(p.resolve()),
                        "size": p.stat().st_size,
                    }
                    for p in images.rglob("*")
                    if p.is_file()
                ],
            },
        )
    try:
        if stereo:
            from openreef.pipeline.stereo import prepare_dataset

            prepare_dataset(
                folder, right, dataset, profile, timestamps=timestamps, link_sources=True
            )
            state["imageCount"] = sum(p.is_file() for p in images.rglob("*"))
            state["rightSource"] = str(right)
            atomic_json(
                workspace / "source-manifest.json",
                {
                    "folders": [str(folder), str(right)],
                    "files": [
                        {
                            "image": str(p.relative_to(images)),
                            "source": str(p.resolve()),
                            "size": p.stat().st_size,
                            "mtimeNs": p.stat().st_mtime_ns,
                        }
                        for p in images.rglob("*")
                        if p.is_file()
                    ],
                },
            )
            atomic_json(status, state)
        with (workspace / "pipeline.log").open("w") as log:
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
            )
            with process.stdout:
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    print(line, end="", flush=True)
            code = process.wait()
        if code:
            raise RuntimeError(f"OpenReef pipeline exited with code {code}")
        # Partial-stage experiments need not produce a GLB.
        artifacts = (
            {role: str(path) for role, path in worker.select_result_artifacts(dataset).items()}
            if "stages" not in options
            else {}
        )
        if joint and (dataset / "joint/diagnostics.json").is_file():
            state["jointDiagnostics"] = json.loads((dataset / "joint/diagnostics.json").read_text())
        state.update(state="completed", artifacts=artifacts, completedAt=worker.utc_now())
    except Exception as error:
        state.update(state="failed", error=str(error)[:1000], completedAt=worker.utc_now())
        atomic_json(status, state)
        raise RuntimeError(f"{error}; retained run: {workspace}") from error
    atomic_json(status, state)
    return {**state, "statusPath": str(status), "logPath": str(workspace / "pipeline.log")}
