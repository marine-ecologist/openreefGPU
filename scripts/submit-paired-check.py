#!/usr/bin/env python3
"""Create an isolated temporary native comparison endpoint and submit its visual check."""

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path


class RunPodRequestError(RuntimeError):
    """Credential-free context for a denied API operation."""


def rejection_detail(error, key):
    raw = error.read(8192).decode("utf-8", errors="replace")
    try:
        data = json.loads(raw)
    except ValueError:
        if "1010" in raw or "cloudflare" in raw.lower():
            return "Request rejected by the network filter (Cloudflare)."
        return "Non-JSON rejection from the server or network filter."
    if not isinstance(data, dict):
        return ""
    parts = [
        str(data[k])
        for k in ("title", "detail", "message", "code")
        if isinstance(data.get(k), (str, int))
    ]
    text = " | ".join(parts).replace(key, "[redacted]")
    text = re.sub(r"\b(?:rpa|rps)_[A-Za-z0-9_-]+", "[redacted]", text)
    return " ".join(text.split())[:400]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", help="Permanent folder relative to openreef-sources")
    parser.add_argument(
        "--reference-endpoint",
        default="93jkogjuc9l6pu",
        help="Read GPU configuration only; never modified",
    )
    parser.add_argument(
        "--image", default="ghcr.io/marine-ecologist/openreef-gpu:0.6.5-gpu.10-paired-native.1"
    )
    parser.add_argument(
        "--endpoint", help="Submit to an existing comparison endpoint; no management API calls"
    )
    parser.add_argument("--pair-limit", type=int)
    parser.add_argument("--products", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--cores", type=int, default=16)
    parser.add_argument("--timeout-hours", type=int, default=8)
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--keep-endpoint", action="store_true")
    parser.add_argument(
        "--diagnose", action="store_true", help="Check access only; creates no endpoints or jobs"
    )
    args = parser.parse_args()
    key = os.environ.get("RUNPOD_API_KEY", "").strip()
    if not key:
        parser.error("Set RUNPOD_API_KEY in this terminal first")
    if not args.source and not args.diagnose:
        parser.error("--source is required unless using --diagnose")
    if args.pair_limit is not None and args.pair_limit < 3:
        parser.error("--pair-limit must be at least 3")
    if not 1 <= args.timeout_hours <= 12 or not 1 <= args.cores <= 64:
        parser.error("Invalid timeout or core count")
    if not args.reference_endpoint.isalnum():
        parser.error("Invalid reference endpoint")

    if args.endpoint and (not args.endpoint.isalnum() or args.endpoint == args.reference_endpoint):
        parser.error("Use a separate paired-native endpoint, not the production reference endpoint")

    def call(url, body=None, method=None, *, operation="API request"):
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 OpenReefGPU/0.6.5",
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            detail = rejection_detail(error, key)
            hint = ""
            if error.code == 403:
                hint = (
                    " Check that this key belongs to the endpoint's account and permits "
                    "endpoint management plus jobs on newly created endpoints. "
                    "A key restricted to jobs on the original endpoint is insufficient."
                )
            elif error.code == 401:
                hint = " Re-enter an enabled RunPod API key in this terminal."
            raise RunPodRequestError(
                f"RunPod denied {operation} (HTTP {error.code}). {detail}{hint}"
                " No credentials printed."
            ) from None

    control = "https://api.runpod.io/v2/serverless"
    if args.diagnose:
        failed = False
        checks = (
            ("management list", control),
            ("reference configuration", control + "/" + args.reference_endpoint),
            (
                "existing endpoint job access",
                "https://api.runpod.ai/v2/" + args.reference_endpoint + "/health",
            ),
        )
        if args.endpoint:
            checks = (
                (
                    "comparison endpoint job access",
                    "https://api.runpod.ai/v2/" + args.endpoint + "/health",
                ),
            )
        for label, url in checks:
            try:
                call(url, operation=label)
                print(json.dumps({"check": label, "result": "OK"}), flush=True)
            except RunPodRequestError as error:
                failed = True
                print(json.dumps({"check": label, "error": str(error)}), flush=True)
        return int(failed)
    owned_endpoint = not bool(args.endpoint)
    if args.endpoint:
        identity = args.endpoint
        print(
            json.dumps({"existingComparisonEndpoint": identity, "managementApiUsed": False}),
            flush=True,
        )
    else:
        original = call(
            control + "/" + args.reference_endpoint,
            operation="reading reference endpoint configuration",
        )
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
        endpoint = call(control, body, operation="creating the temporary comparison endpoint")
        identity = endpoint["id"]
        print(
            json.dumps(
                {
                    "temporaryEndpoint": identity,
                    "referenceEndpointUnchanged": args.reference_endpoint,
                }
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
            operation="submitting the comparison job",
        )
    except Exception:
        if owned_endpoint:
            try:
                call(
                    control + "/" + identity,
                    method="DELETE",
                    operation="removing the temporary endpoint",
                )
            except RunPodRequestError as cleanup_error:
                print(
                    f"Cleanup needed for temporary endpoint {identity}: {cleanup_error}", flush=True
                )
        raise
    record = {"endpoint": identity, "job": job["id"], "source": args.source, "image": args.image}
    record_path = Path.cwd() / ("paired-native-" + job["id"] + ".json")
    record_path.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record), flush=True)
    if not args.wait:
        return
    try:
        while True:
            result = call(api + "/status/" + job["id"], operation="checking comparison job status")
            if result["status"] in {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"}:
                record["result"] = result
                record_path.write_text(json.dumps(record, indent=2) + "\n")
                print(json.dumps(result, indent=2), flush=True)
                if owned_endpoint and not args.keep_endpoint:
                    call(
                        control + "/" + identity,
                        method="DELETE",
                        operation="removing the temporary endpoint",
                    )
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
    except RunPodRequestError as error:
        raise SystemExit(str(error)) from None
