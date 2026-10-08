#!/usr/bin/env python3
"""Run the OpenReef desktop pipeline with the deployed cloud output profile."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

STAGE_LINE = re.compile(r"^\[(\d+)/(\d+)\] (.+)$")
PIPELINE_STAGE_COUNT = 9
CLOUD_STAGE_NAMES = (
    "Feature extraction",
    "Sequential matching",
    "Sparse reconstruction",
    "Undistort / PINHOLE",
    "OpenMVS import",
    "Dense point cloud",
    "Surface mesh",
    "Texture mesh",
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description=(
            "Benchmark a local OpenReef checkout with the same reconstruction and "
            "output settings as the deployed openreefGPU compact worker."
        )
    )
    value.add_argument("dataset", type=Path, help="Dataset containing the images/ folder")
    value.add_argument(
        "--openreef-dir",
        type=Path,
        default=Path.home() / "openreef",
        help="Desktop OpenReef checkout (default: ~/openreef)",
    )
    value.add_argument(
        "--cores",
        type=int,
        default=max(1, (os.cpu_count() or 2) - 1),
        help="CPU threads supplied to OpenReef (default: logical CPUs minus one)",
    )
    value.add_argument(
        "--output",
        type=Path,
        help="Benchmark JSON path (default: dataset/openreef-benchmark-<time>.json)",
    )
    value.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the exact pipeline command without starting reconstruction",
    )
    return value


def pipeline_command(args: argparse.Namespace) -> list[str]:
    launcher = args.openreef_dir.expanduser().resolve() / "scripts" / "openreef-pipeline.sh"
    if not launcher.is_file():
        raise FileNotFoundError(f"OpenReef pipeline launcher not found: {launcher}")
    return [
        str(launcher),
        str(args.dataset.expanduser().resolve()),
        "--force",
        "--no-gpu",
        "--dense-compact",
        "--no-dense-original",
        "--max-image-size",
        "3200",
        "--resolution-level",
        "1",
        "--max-resolution",
        "2560",
        "--texture-resolution-level",
        "0",
        "--max-texture-size",
        "4096",
        "--texture-sharpness",
        "0",
        "--no-global-seam-leveling",
        "--no-local-seam-leveling",
        "--cores",
        str(args.cores),
    ]


def cloud_comparable(stage_seconds: dict[str, float]) -> dict[str, float]:
    comparable = {name: stage_seconds[name] for name in CLOUD_STAGE_NAMES if name in stage_seconds}
    if "Sparse reconstruction" in comparable:
        comparable["Sparse reconstruction"] += stage_seconds.get("MarkerTags detected", 0.0)
    return comparable


def pipeline_stage(line: str) -> str | None:
    """Return a top-level pipeline stage, ignoring nested task progress labels."""
    match = STAGE_LINE.fullmatch(line)
    if not match or int(match.group(2)) != PIPELINE_STAGE_COUNT:
        return None
    return match.group(3)


def mac_hardware() -> dict[str, str]:
    """Return stable, non-identifying Mac hardware fields when available."""
    if platform.system() != "Darwin":
        return {}
    try:
        completed = subprocess.run(
            ["system_profiler", "SPHardwareDataType", "-json"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        records = json.loads(completed.stdout).get("SPHardwareDataType", [])
        hardware = records[0] if records else {}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return {}
    allowed = (
        "machine_name",
        "machine_model",
        "chip_type",
        "number_processors",
        "physical_memory",
    )
    return {key: str(hardware[key]) for key in allowed if hardware.get(key)}


def main() -> int:
    args = parser().parse_args()
    if args.cores < 1:
        raise SystemExit("--cores must be at least 1")
    command = pipeline_command(args)
    print("Local cloud-profile benchmark command:", flush=True)
    print(subprocess.list2cmdline(command), flush=True)
    if args.dry_run:
        return 0

    dataset = args.dataset.expanduser().resolve()
    if not (dataset / "images").is_dir():
        raise SystemExit(f"Dataset does not contain an images folder: {dataset}")
    existing_outputs = [
        path.name
        for path in (dataset / "colmap", dataset / "openmvs", dataset / "models")
        if path.exists()
    ]
    if existing_outputs:
        raise SystemExit(
            "A valid benchmark needs a clean dataset workspace containing only source images. "
            f"Existing output folders found: {', '.join(existing_outputs)}"
        )

    started_at = datetime.now(timezone.utc)
    started = time.monotonic()
    active_name: str | None = None
    active_started = started
    stage_seconds: dict[str, float] = {}
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    try:
        for line in process.stdout:
            print(line, end="", flush=True)
            stage_name = pipeline_stage(line.strip())
            if stage_name is None:
                continue
            now = time.monotonic()
            if active_name is not None:
                stage_seconds[active_name] = round(now - active_started, 3)
            active_name = stage_name
            active_started = now
    except KeyboardInterrupt:
        process.terminate()
        process.wait()
        return 130

    return_code = process.wait()
    finished = time.monotonic()
    if active_name is not None:
        stage_seconds[active_name] = round(finished - active_started, 3)
    comparable = cloud_comparable(stage_seconds)
    timestamp = started_at.strftime("%Y%m%dT%H%M%SZ")
    output = args.output or dataset / f"openreef-benchmark-{timestamp}.json"
    result = {
        "schemaVersion": 1,
        "profile": "openreefGPU compact 0.6.5",
        "state": "completed" if return_code == 0 else "failed",
        "returnCode": return_code,
        "startedAt": started_at.isoformat(),
        "elapsedSeconds": round(finished - started, 3),
        "host": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "logicalCpuCount": os.cpu_count(),
            "coresUsed": args.cores,
            "macHardware": mac_hardware(),
        },
        "settings": {
            "colmapGpu": False,
            "denseOutput": "compact-only",
            "maxImageSize": 3200,
            "denseResolutionLevel": 1,
            "maxDenseResolution": 2560,
            "textureResolutionLevel": 0,
            "maxTextureSize": 4096,
            "textureSharpness": 0,
            "globalSeamLeveling": False,
            "localSeamLeveling": False,
        },
        "stageSeconds": stage_seconds,
        "cloudComparableStageSeconds": comparable,
        "computeOnlySeconds": round(sum(comparable.values()), 3),
        "notes": [
            "Drive transfer is excluded.",
            "MarkerTags time is combined with Sparse reconstruction because the cloud timer "
            "does not expose MarkerTags as a separate stage.",
            "COLMAP GPU is disabled because the current macOS COLMAP build has no GPU support.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"\nBenchmark record: {output}", flush=True)
    print(f"Compute-only total: {result['computeOnlySeconds']:.1f}s", flush=True)
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
