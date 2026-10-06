# M5 MacBook Air benchmark

The exported bundle runs the desktop OpenReef pipeline against the same 248
`reefplot/images` photographs and the same compact output settings used in the
RunPod comparisons. Google Drive transfer, worker startup, and output upload are
not part of the local compute total.

## Run tomorrow

1. Copy `openreef-m5-air-benchmark.zip` to the M5 MacBook Air and unzip it.
2. Confirm the desktop OpenReef checkout is installed at `~/openreef`.
3. Double-click `run-macos-benchmark.command`.
4. Return `results/openreef-benchmark-mac.json` for inclusion in
   `BENCHMARKS.md`.

If OpenReef is somewhere else, run this in Terminal from the unzipped folder:

```sh
OPENREEF_DIR=/path/to/openreef ./run-macos-benchmark.command
```

The runner verifies all 248 source-image checksums before starting. It records
the Mac model, chip, installed RAM, CPU count, cores used, every pipeline stage,
and the eight-stage compute-only total. It uses all logical CPU cores except one,
matching the existing M2 Max test policy.
