#!/usr/bin/env python3
"""Create an isolated temporary native comparison endpoint and submit its visual check."""

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", required=True, help="Permanent folder relative to openreef-sources"
    )
    parser.add_argument(
        "--reference-endpoint",
        default="93jkogjuc9l6pu",
        help="Read GPU configuration only; never modified",
    )
    parser.add_argument(
        "--image", default="ghcr.io/marine-ecologist/openreef-gpu:0.6.5-gpu.10-paired-native.1"
    )
    parser.add_argument("--pair-limit", type=int)
    parser.add_argument("--products", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--cores", type=int, default=16)
    parser.add_argument("--timeout-hours", type=int, default=8)
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--keep-endpoint", action="store_true")
    args = parser.parse_args()
    key = os.environ.get("RUNPOD_API_KEY")
    if not key:
        parser.error("Set RUNPOD_API_KEY in this terminal first")
    if args.pair_limit is not None and args.pair_limit < 3:
        parser.error("--pair-limit must be at least 3")
    if not 1 <= args.timeout_hours <= 12 or not 1 <= args.cores <= 64:
        parser.error("Invalid timeout or core count")
    if not args.reference_endpoint.isalnum():
        parser.error("Invalid reference endpoint")

    def call(url, body=None, method=None):
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            method=method,
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
            return json.loads(raw) if raw else None

    control = "https://api.runpod.io/v2/serverless"
    original = call(control + "/" + args.reference_endpoint)
    if isinstance(original.get("data"), dict):
        original = original["data"]
    gpu = original.get("gpu")
    if not gpu or not gpu.get("pools"):
        raise RuntimeError(
            "Reference endpoint has no readable GPU pool configuration; nothing was changed"
        )
    gpu = {
        k: v
        for k, v in gpu.items()
        if k in {"pools", "excludedTypes", "allowedCudaVersions", "minCudaVersion"}
        and v not in (None, [])
    }
    gpu["count"] = 1
    body = {
        "name": "openreef-paired-native-check-" + str(int(time.time())),
        "image": args.image,
        "type": "QUEUE",
        "gpu": gpu,
        "workers": {"min": 0, "max": 1, "idleTimeout": 5},
        "scaling": {"type": "QUEUE_DELAY", "queueDelay": 4},
        "dataCenterIds": ["US-NE-1"],
        "mounts": {"network": [{"volumeId": "s8baqr2m12"}]},
        "disk": 500,
        "timeout": args.timeout_hours * 3600000,
        "env": {},
    }
    endpoint = call(control, body)
    identity = endpoint["id"]
    print(
        json.dumps(
            {"temporaryEndpoint": identity, "referenceEndpointUnchanged": args.reference_endpoint}
        ),
        flush=True,
    )
    payload = {"source": args.source, "products": args.products, "cores": args.cores}
    if args.pair_limit is not None:
        payload["pair_limit"] = args.pair_limit
    api = "https://api.runpod.ai/v2/" + identity
    try:
        job = call(
            api + "/run",
            {
                "input": {"paired_native_check": payload},
                "policy": {
                    "executionTimeout": args.timeout_hours * 3600000,
                    "ttl": (args.timeout_hours + 2) * 3600000,
                },
            },
        )
    except Exception:
        call(control + "/" + identity, method="DELETE")
        raise
    record = {"endpoint": identity, "job": job["id"], "source": args.source, "image": args.image}
    record_path = Path.cwd() / ("paired-native-" + job["id"] + ".json")
    record_path.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record), flush=True)
    if not args.wait:
        return
    try:
        while True:
            result = call(api + "/status/" + job["id"])
            if result["status"] in {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"}:
                record["result"] = result
                record_path.write_text(json.dumps(record, indent=2) + "\n")
                print(json.dumps(result, indent=2), flush=True)
                if not args.keep_endpoint:
                    call(control + "/" + identity, method="DELETE")
                    print("Temporary endpoint removed; volume outputs retained.")
                return 0 if result["status"] == "COMPLETED" else 1
            time.sleep(5)
    except KeyboardInterrupt:
        print(
            "Stopped polling; the job continues with zero minimum workers. Saved IDs: "
            + str(record_path)
        )
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.HTTPError as error:
        raise SystemExit(
            "RunPod API HTTP " + str(error.code) + "; no credentials printed"
        ) from error
