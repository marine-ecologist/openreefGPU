#!/usr/bin/env python3
"""Submit Drive-free runs to an OpenReefGPU serverless endpoint."""

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True, help="RunPod endpoint ID")
    parser.add_argument(
        "--folder",
        action="append",
        required=True,
        help="Folder relative to openreef-sources; repeat for independent runs",
    )
    parser.add_argument(
        "--joint", action="store_true", help="Estimate two cameras without a fixed rig"
    )
    parser.add_argument(
        "--right-folder",
        help="Second camera source folder for joint or calibrated stereo reconstruction",
    )
    parser.add_argument("--profile", help="Calibration JSON path relative to openreef-sources")
    parser.add_argument("--timestamps", help="Timing JSON path relative to openreef-sources")
    parser.add_argument("--max-image-size", type=int)
    parser.add_argument("--max-resolution", type=int)
    parser.add_argument("--cores", type=int)
    parser.add_argument("--sequential-overlap", type=int)
    parser.add_argument("--stages", help="Comma-separated pipeline stages for a partial run")
    parser.add_argument(
        "--count", type=int, help="Consecutive image subset size (single camera only)"
    )
    parser.add_argument("--start", type=int, default=0, help="Zero-based image offset")
    parser.add_argument("--resume-run", help="Retained joint run UUID with completed matching")
    parser.add_argument(
        "--execution-timeout", type=int, default=14400, help="Maximum execution seconds"
    )
    parser.add_argument("--wait", action="store_true")
    args = parser.parse_args()
    if not args.endpoint.isalnum():
        parser.error("endpoint must be a RunPod endpoint ID")
    if args.joint and (not args.right_folder or args.profile or args.timestamps):
        parser.error("--joint requires --right-folder and does not use a rig profile")
    if args.right_folder and (len(args.folder) != 1 or (not args.profile and not args.joint)):
        parser.error("stereo requires one --folder, --right-folder and --profile")
    if args.right_folder and (args.count is not None or args.start):
        parser.error("Stereo subsets require preselected, timestamp-aligned folders")
    if not args.right_folder and (args.profile or args.timestamps):
        parser.error("--profile/--timestamps require --right-folder")
    key = os.environ.get("RUNPOD_API_KEY")
    if not key:
        parser.error("Set RUNPOD_API_KEY in your terminal environment first")
    versions = json.loads((Path(__file__).resolve().parents[1] / "versions.json").read_text())
    base = f"https://api.runpod.ai/v2/{args.endpoint}"

    def call(route, body=None):
        request = urllib.request.Request(
            base + route,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)

    pending = []
    for folder in args.folder:
        volume = {"folder": folder, "start": args.start}
        if args.count is not None:
            volume["count"] = args.count
        if args.right_folder:
            volume.update(right_folder=args.right_folder)
            if args.joint:
                volume["mode"] = "joint"
            else:
                volume["profile"] = args.profile
            if args.timestamps:
                volume["timestamps"] = args.timestamps
        if args.resume_run:
            if not args.joint:
                parser.error("--resume-run requires --joint")
            volume["resume_run"] = args.resume_run
        payload = {
            "schema_version": 1,
            "openreef_version": versions["openreef"]["version"],
            "openreef_gpu_version": versions["openreefGPU"]["version"],
            "volume": volume,
            "pipeline": {
                k: getattr(args, k)
                for k in (
                    "max_image_size",
                    "max_resolution",
                    "cores",
                    "sequential_overlap",
                    "stages",
                )
                if getattr(args, k) is not None
            },
        }
        result = call(
            "/run",
            {
                "input": payload,
                "policy": {
                    "executionTimeout": args.execution_timeout * 1000,
                    "ttl": (args.execution_timeout + 7200) * 1000,
                },
            },
        )
        print(json.dumps({"folder": folder, **result}), flush=True)
        pending.append(result["id"])
    failed = False
    if args.wait:
        try:
            while pending:
                for job in pending[:]:
                    result = call("/status/" + job)
                    if result["status"] in {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"}:
                        print(json.dumps(result, indent=2), flush=True)
                        failed |= result["status"] != "COMPLETED"
                        pending.remove(job)
                if pending:
                    time.sleep(5)
        except KeyboardInterrupt:
            print("Stopped polling; submitted RunPod jobs continue running.")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.HTTPError as error:
        raise SystemExit(f"RunPod request failed: HTTP {error.code}") from error
