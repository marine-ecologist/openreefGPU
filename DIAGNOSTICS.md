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

## First completed 200-image run

User-reported results from `/tmp/openreef-diagnostic-e98e99719a41/results`:

| Run | Seconds | Return code |
| --- | ---: | ---: |
| Full original+compact pipeline, Ceres CUDA, 32 threads | 1137.373 | 0 |
| Sparse replay, 8 threads | 459.423 | 0 |
| Sparse replay, 32 threads | 560.348 | 0 |

The 8-thread sparse replay took approximately 18% less elapsed time. Both used the
same source database on the same worker, but ran once in fixed order. Model coverage,
reprojection errors and per-thread measurements still need inspection before selecting
a production default. These timings do not justify reducing dense-stage threads.
Recorded cgroup OOM counters were zero. The original full-survey dense crash was not
reproduced on this 200-image subset.

Preserve the current worker until results are collected. Run on the Mac:

```bash
python3 /Users/heliopora/Documents/ChatGPT/openreef/openreefGPU/setup/setup.py \
  --collect /tmp/openreef-diagnostic-e98e99719a41/results \
  --ssh 'ssh mt79ud5ahb7i97-644122c5@ssh.runpod.io -i ~/.ssh/id_ed25519'
```

Collection reads existing logs and models, runs only the lightweight COLMAP model
analyzer, and saves a summary plus raw diagnostic logs/thread samples under
`~/Library/Application Support/OpenReefGPU/diagnostic-reports/`. It neither stages
photos nor runs sparse/dense reconstruction. The original evidence remains remote.

## Sparse-only restart after loss of the first worker

The first worker and its temporary results became unavailable before collection.
Only its posted timing/return-code/OOM summaries were recovered. Those totals do
not establish native hot functions or equivalent registered-image coverage.

The replacement launcher supports `--profile-sparse`. It prompts for the NEW
worker's SSH connection and needs no Mac dependency reinstall:

```bash
python3 /Users/heliopora/Documents/ChatGPT/openreef/openreefGPU/setup/setup.py --profile-sparse
```

Run only on an idle worker. It refuses to run while another native reconstruction
is detected. Preparation uses the installed OpenReef pipeline's explicit
`--stages features,matching` selector; mapping runs COLMAP directly. There is no
dense reconstruction, mesh generation, texture generation or splat training.

The current 200-image source is compared at 4 and 8 threads. The old 248-image
`images` folder is verified against `benchmarks/reefplot-248.sha256` BEFORE
compute starts, then mapped at 8 threads. Missing/changed reference photos stop
the experiment instead of silently substituting another dataset. Each mapper
receives a separate backup of its dataset's fixed feature/matching database.
No experimental matching/quality/BA thresholds are changed.

Metrics include CPU model/affinity/cgroup limits, per-thread CPU and wait states,
process I/O, feature/match counts, registered-image counts, reprojection error and
track observations. When available and permitted, native `perf stat` and `perf
record` produce counters and symbol hotspots. Missing/denied profiling is explicit;
phase windows and thread activity alone do not identify exact native functions.
Profiler permissions/system settings are never relaxed.

The runner writes only to ephemeral working disk and Drive diagnostics. It reads
existing network cache entries but downloads cache misses to ephemeral disk, avoiding
additional network-volume quota use. Reports are saved within the connected Drive
root at `diagnostics/sparse-profile-<id>/`. Each completed stage uploads its logs;
a live snapshot is attempted every minute during processing. Final evidence and
failure state are uploaded on normal Python exception paths as well. Abrupt worker
termination can still lose work since the most recent successful upload.

These are paid runs. The editing session could not launch them: outbound SSH was
rejected with `Operation not permitted`, no RunPod management API key was present,
and the previously supplied worker had already been reported unavailable. A new
worker connection remains necessary. Active workers continue billing after the
script finishes until the endpoint's active-worker setting is returned to zero.

### Drive destination creation fix

The first sparse-only attempt failed at the hardware checkpoint because the
worker's storage writer resolves existing parent folders and does not create
missing directories. The profiler now explicitly creates/reuses both
`diagnostics` and its unique `sparse-profile-*` child before uploading. A
regression test exercises the real Drive folder resolver and byte writer against
a simulated empty Drive, then verifies that repeated checkpoints reuse folders
and files. This failure happened before feature extraction or mapping.

### Optional reference discovery

The historical reference is no longer assumed to be at `reefplot/images`.
The profiler checks that preferred path and searches at most 20 folders, to two
levels under the configured root, excluding jobs and diagnostics. Filename matches
only nominate a candidate; staged photographs must still match all saved SHA-256
checksums. Missing or changed historical photos are recorded in `reference.json`
and do not stop the current 200-image profiling. Authentication/network failures
still surface as errors. When unavailable, the historical comparison remains
explicitly unverified; the current 4/8-thread results are not presented as a
controlled comparison with the old 248-image benchmark.

### Explicit Drive source IDs

The user confirmed root `1EuO8J5TBR55UQOxTEU_X_fOXeZZVHkwr` and current-survey
folder `19pJux_ONjbFHmflWQPtJwL1ayYd4zczs` (`orbicella-rockbottom-03-11-26`).
The saved OAuth configuration matches the supplied root. The profiler now validates
the source folder metadata and pins current-source resolution to that ID, including
an optional images child. This does not establish that the folder contains the old
248-image historical reference; reference identity remains checksum-verified.

## Completed detailed current-dataset comparison

Drive report: `diagnostics/sparse-profile-fc911f847808430488897c8d43105aba`.
User supplied complete successful 4-thread and 8-thread metrics. Historical
reference mapping was not present in the supplied completion output.

| Metric | 4 threads | 8 threads |
| --- | ---: | ---: |
| Mapper elapsed seconds | 410.607944 | 467.261217 |
| Largest model registered images | 200 | 200 |
| Largest model points | 113351 | 113303 |
| Largest model observations | 495908 | 495574 |
| Mean reprojection error, pixels | 1.311712 | 1.312221 |
| Retriangulation/global log windows, seconds | 337.802625 | 393.707280 |
| Container CPU seconds, counter delta | 1397.428458 | 2584.014291 |
| Container system CPU seconds, counter delta | 217.712426 | 971.499625 |

Eight threads were 13.80% slower and consumed 84.91% more container CPU time.
The extra 55.90 seconds in logged retriangulation/global windows account for
98.68% of the 56.65-second elapsed increase. These windows include intervening
work; they are not pure BA timings or a native function profile. System CPU rose
4.46-fold, consistent with increased parallel/scheduling/synchronization cost,
but no specific library-level contention mechanism was established. Container
CPU covers all processes. Per-thread samples miss short-lived threads and must
not be treated as exact process CPU accounting. No cgroup throttling/OOM was seen.
Both runs also produced the same small 2-image/492-point model.

A read-only inspection of the saved LOCAL MAC 248-image benchmark found 1916587
keypoints and 591018 verified correspondences. Its two models registered 82 and
156 images; the largest contained 51187 points, 222975 observations and mean
reprojection error 0.990978 pixels. The current 200-image database has 2029320
keypoints and 1024481 verified correspondences, and its largest model contains
about 2.2 times as many observations. This supports workload complexity as a
contributor. The Mac CPU-feature reconstruction is not assumed identical to the
historical cloud GPU models, so this does not isolate the old-cloud timing gap.

Current conclusion: prioritize 4-thread sparse mapping on this worker class over
8/32-thread settings; do not reduce all pipeline stages globally. Exact native
hotspots remain unknown because perf was unavailable. Larger VRAM alone is not
a demonstrated solution. Retrieve the persisted hardware, binary options and
raw logs before further paid trials.

Drive-only collection, with no running RunPod worker required:

```bash
python3 /Users/heliopora/Documents/ChatGPT/openreef/openreefGPU/setup/setup.py \
  --collect-drive diagnostics/sparse-profile-fc911f847808430488897c8d43105aba
```

The collector verifies the downloaded archive size and available Drive MD5,
retains raw files and writes a local summary. The editing session attempted this
read but could not resolve Google hostnames; no live report was downloaded there.
