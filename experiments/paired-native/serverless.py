"""Isolated serverless entry point. Never changes the production worker or sources."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path


def source_path(value):
    root = (
        Path(os.environ.get("OPENREEF_VOLUME_ROOT", "/runpod-volume")).resolve()
        / "openreef-sources"
    )
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ValueError("source must be relative to openreef-sources")
    source = (root / value).resolve()
    if source == root or not source.is_relative_to(root):
        raise ValueError("source escapes permitted root")
    if not (source / "pairs.csv").is_file() or not all((source / c).is_dir() for c in "AB"):
        raise ValueError("Paired source is incomplete")
    return source


def handler(job):
    payload = job.get("input", {}).get("paired_native_check")
    if not isinstance(payload, dict):
        raise ValueError("This temporary worker accepts paired_native_check only")
    if set(payload) - {"source", "pair_limit", "products", "cores"}:
        raise ValueError("Unknown comparison option")
    source = source_path(payload.get("source"))
    limit = payload.get("pair_limit")
    cores = payload.get("cores", 16)
    products = payload.get("products", True)
    if limit is not None and (type(limit) is not int or not 3 <= limit <= 10000):
        raise ValueError("Invalid pair limit")
    if type(cores) is not int or not 1 <= cores <= 64 or type(products) is not bool:
        raise ValueError("Invalid comparison settings")
    mount = Path(os.environ.get("OPENREEF_VOLUME_ROOT", "/runpod-volume")).resolve()
    run_id = "paired-native-" + str(uuid.uuid4())
    root = mount / "openreef-runs" / run_id
    command = [
        sys.executable,
        "-u",
        str(Path(__file__).with_name("compare.py")),
        "--source",
        str(source),
        "--output",
        str(root),
        "--run",
        "--cores",
        str(cores),
    ]
    if limit:
        command += ["--pair-limit", str(limit)]
    if products:
        command += ["--products"]
    # The log lives beside the new dataset; all outputs survive worker shutdown.
    root.parent.mkdir(exist_ok=True)
    log = root.parent / (run_id + ".log")
    print(json.dumps({"run": str(root), "log": str(log)}), flush=True)
    with log.open("w") as stream:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
        )
        with process.stdout:
            for line in process.stdout:
                stream.write(line)
                stream.flush()
                print(line, end="", flush=True)
        code = process.wait()
    if code:
        raise RuntimeError(f"Native paired check exited {code}; retained run {root}; log {log}")
    report = json.loads((root / "comparison.json").read_text())
    state = json.loads((root / "run.json").read_text())
    return {
        "run": str(root),
        "state": state,
        "comparison": report,
        "artifacts": [
            str(p)
            for pattern in ("*.glb", "*_orthomap.png", "*_orthomap.txt")
            for p in (root / "openmvs").glob(pattern)
        ],
        "orientedSparseModel": str(root / "colmap/sparse/checked/points3D.ply"),
        "log": str(log),
    }


if __name__ == "__main__":
    import runpod

    runpod.serverless.start({"handler": handler})
