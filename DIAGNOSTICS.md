# Orbicella diagnostic run

## Findings from the supplied logs

The long sparse run (job prefix a805356d) and dense failure (worker
3u9oa79xim2svw) have different identifiers. Their records must be correlated before
attributing every timing to the failed attempt.

- Staging consumed 73m05s. At approximately 6 GB, the average was about 1.4 MB/s.
  Cache misses, retries and Drive/network performance need transfer logs to distinguish.
  Existing code already downloads with eight concurrent workers; increasing concurrency
  without measuring is not a demonstrated fix.
- COLMAP was registering images sequentially during the one-core samples. Its
  incremental mapper does not parallelize the entire reconstruction across the requested
  32 threads. GPU bundle adjustment does not accelerate every registration operation.
- The measured cgroup had no CPU quota and reported zero throttling. The advertised
  eight vCPUs are not evidence of an enforced eight-core limit in that cgroup.
- The log reached 882 registered frames before retriangulation/global bundle adjustment.
  Subsequent registration reached at least 1062. The old Mac benchmark reconstructed
  smaller disconnected models, so it is not a controlled comparison with this model.
- The failed worker stopped DURING geometric-consistent depth-map estimation at
  1236/approximately 1446 maps (85.48%). "Timing finished" marks timer completion,
  including failure. It does not mean dense reconstruction succeeded.
- Exit 245 is consistent with a Python wrapper propagating native -11/SIGSEGV through
  an eight-bit process exit status. It does not establish the native signal or whether
  CUDA, a race, memory pressure, or input data caused it. A native backtrace is still needed.

## Reproduce on RunPod

The new image includes this runner. Alternatively, copy `scripts/diagnose-runpod.py`
into `/opt/openreefgpu/scripts/` on an existing OpenReef worker, and copy `versions.json`
to `/opt/openreefgpu/versions.json`. Use a separate idle worker so the diagnostic does
not compete with another job. Run with the worker's existing Drive environment:

```bash
cd /opt/openreefgpu
python3 scripts/diagnose-runpod.py \
  --source orbicella-rockbottom-03-11-26 \
  --count 200 \
  --start 0 \
  --cores 32 \
  --compare-sparse \
  --output "/tmp/openreef-diagnostic-200-$(date -u +%Y%m%dT%H%M%SZ)"
```

This performs real GPU work and incurs the worker's normal charges. It selects the
first 200 consecutive unique photos in filename order, downloads only those photos,
and verifies size and available Drive MD5 checksums. If the folder has an `images`
child instead of direct photos, the runner tries that child. No Drive originals are
changed and no duplicate subset folder is created on Drive.

The baseline retains full-project/original+compact settings. It records staging time,
commands, exit statuses, per-thread CPU ticks, GPU memory, hardware and cgroup memory
failure counters. Pipeline output is in `baseline/pipeline.log`; resource samples are
in `baseline/resources.jsonl`. The workspace remains after failure.

Sparse replay uses separate SQLite backup copies of the same database and empty model
folders for 8 and 32 threads. It records registered-image/reprojection-error summaries
with `colmap model_analyzer`. These comparisons exclude staging, feature extraction
and matching. Run order/cache effects mean a single comparison is exploratory.

A successful 200-image dense run does not rule out a size-dependent failure at 1493
images. If the subset reproduces the crash, use its logged native DensifyPointCloud
command in a separate copied OpenMVS workspace with gdb (`run`, then
`thread apply all bt`) to obtain the native stack. Confirm CPU/GPU selection flags
against that installed binary's `--help` before attempting a CPU comparison.

## Changes and optimization decisions

Version 0.6.5-gpu.8 preserves failure logs and available sparse-model files in a
bounded archive under the job's `diagnostics` storage folder before workspace cleanup.
It excludes images, database and dense intermediates and is not a full resume checkpoint.
The original processing exception remains intact if diagnostic upload also fails.

Production solver/thread/dense settings remain unchanged pending measured comparisons.
Caspar is a candidate for bundle-adjustment testing, but cannot remove all sequential
registration work. Do not reduce geometric consistency or reconstruction resolution
and describe that as an equivalent-quality optimization.

No live Drive subset or GPU benchmark was completed from the editing session: outbound
DNS resolution to Google and RunPod failed. The specific crash cause and any speedup
remain unverified until the runner executes remotely. Do not deploy only the API's
new version declaration; build/select the matching gpu.8 image before submitting gpu.8 jobs.
