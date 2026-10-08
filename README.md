# openreefGPU

`openreefGPU` is the cloud edition of [OpenReef](https://github.com/marine-ecologist/openreef).
It provides a browser workflow, a Cloudflare API broker, and scale-to-zero RunPod GPU processing.
The OpenReef desktop repository remains the source of truth for reconstruction and model-generation
code.

```text
Browser → Google Drive → Cloudflare API → RunPod GPU
        → pinned OpenReef pipeline → GLB → browser viewer
```

## Live deployment

- Browser: <https://marine-ecologist.github.io/openreefGPU/>
- API broker: <https://openreef-gpu-api.openreef-gpu.workers.dev/v1/health>
- RunPod endpoint: `openreef-gpu` (`93jkogjuc9l6pu`)
- RunPod source cache: `openreef-serverless-cache` (10 GB Standard, US-NE-1)
- Storage root: the dedicated `reefplot` folder in Google Drive; existing survey images remain in
  `reefplot/images`, and cloud job state and outputs are written below `reefplot/jobs`.

The browser build receives the broker URL through the GitHub Actions repository variable
`OPENREEF_API_URL`. The initial smoke-test deployment has broker authentication disabled; enable
Cloudflare Access before treating the URL as an unattended public service.

## Product boundary

- **OpenReef** is the desktop application and canonical Python reconstruction pipeline.
- **openreefGPU** owns cloud authentication, job orchestration, RunPod packaging, browser upload,
  progress, and web viewing.
- Pipeline fixes belong in OpenReef first. openreefGPU consumes a released OpenReef commit; it does
  not maintain a second drifting copy of the pipeline.

The current cloud release is `0.6.4-gpu.4`, based on OpenReef `0.6.4`. The suffix may advance for
cloud-only changes (`gpu.2`, `gpu.3`, `gpu.4`) without pretending the desktop pipeline changed.

## Version contract

[`versions.json`](versions.json) is the single version lock. It records:

- the openreefGPU version;
- the compatible OpenReef release, tag, and immutable commit;
- the pinned COLMAP commit and required Caspar build contract;
- the pinned OpenMVS release and Blackwell-compatible commit;
- the pinned CGAL release and commit required by that OpenMVS build;
- the job and result schema versions.

The RunPod Docker image fetches that exact OpenReef commit and verifies its package version during
the image build. The API includes both versions in every job, and the worker refuses jobs whose
versions do not match its image. CI also checks `package.json`, Cloudflare configuration, Docker
arguments, the actual pinned OpenReef checkout, and the newest stable OpenReef release tag. A new
desktop release therefore blocks openreefGPU's main build until its compatibility pin is updated.

For a new desktop release:

1. Release and tag OpenReef first.
2. Update the OpenReef version, tag, and commit in `versions.json`.
3. Set the openreefGPU version to `<openreef-version>-gpu.1`.
4. Update the three version build arguments in `worker/Dockerfile` and the two version variables in
   `cloud/api/wrangler.jsonc`.
5. Run `npm run check:versions -- --openreef-dir /path/to/openreef`.
6. Rebuild the RunPod image and run an end-to-end smoke test before releasing openreefGPU.

This makes version drift a build error instead of a runtime surprise.

## Components

- `app/`: openreefGPU browser interface and Three.js viewer.
- `cloud/api/`: Cloudflare Worker API and Google Drive/SharePoint adapters.
- `worker/`: RunPod handler and CUDA/OpenMVS image definition.
- `scripts/check-versions.mjs`: local and CI version-contract check.
- `CLOUD_DEPLOYMENT.md`: Google Drive, Cloudflare, RunPod, and frontend deployment.

## Local web development

```bash
npm install
npm run check:versions -- --openreef-dir /Users/rof011/openreef
npm run dev
```

Set `NEXT_PUBLIC_OPENREEF_API_URL` when testing cloud submission. Without it, the viewer and example
models still work but GPU submission remains disabled.

### Local compute benchmark

Use the benchmark helper when comparing Apple Silicon machines with a completed RunPod job. It
runs the desktop pipeline with the deployed compact output profile, disables COLMAP GPU for the
current macOS CPU-only build, and writes per-stage plus compute-only timings to JSON. Google Drive
transfer is not included.

```bash
python3 scripts/benchmark-local.py /path/to/dataset \
  --openreef-dir /Users/rof011/openreef
```

Use the generated `openreef-benchmark-*.json` records for the M2, M5, and cloud comparison. The
helper combines local MarkerTag time with sparse reconstruction to match the cloud timer. Run each
machine against a clean dataset workspace containing the same `images/` folder; the helper refuses
existing `colmap/`, `openmvs/`, or `models/` outputs so cached work cannot distort the result.
The 248-image OpenReef 0.6.3 baseline and OpenReef 0.6.4 Caspar cloud-GPU
matrix are recorded in [BENCHMARKS.md](BENCHMARKS.md). Apple Silicon results
remain local reference measurements and are excluded from the cloud-GPU ranking.

For repeated cloud benchmarks, attach a Runpod volume to the endpoint. The worker automatically
uses `/runpod-volume/openreef-source-cache`: the first run fills it from Google Drive, and later runs
stage matching source images from the persistent cache. The browser reports cache hits and staging
time; compute-only benchmark totals continue to exclude source transfer and output upload.

## RunPod image

Build from this repository root:

```bash
docker build --platform linux/amd64 \
  -f worker/Dockerfile \
  -t YOUR_REGISTRY/openreef-gpu:0.6.4-gpu.4 .
```

The Dockerfile packages OpenReef `0.6.4` from its pinned commit, a pinned COLMAP build compiled with
the experimental Caspar GPU bundle-adjustment backend, CUDA OpenMVS, and the small openreefGPU job
adapter. See [CLOUD_DEPLOYMENT.md](CLOUD_DEPLOYMENT.md) for complete setup and the required smoke
test.

After the repository is published on GitHub, run the **Publish RunPod worker image** workflow. It
publishes the pinned Linux image to GitHub Container Registry as both
`ghcr.io/<owner>/openreef-gpu:0.6.4-gpu.4` and `:latest`, ready for a RunPod Serverless template.

The worker pins COLMAP commit `68b722b` (including the 2026-10-07 Caspar kernel fixes), OpenMVS
2.4.0 plus its upstream Blackwell compatibility fix, and CGAL 6.0.1. COLMAP compiles Caspar for
Turing through Blackwell (`sm_75`, `sm_80`, `sm_86`, `sm_89`, `sm_90`, `sm_100`, `sm_103`, and
`sm_120`), and the worker caps native pipeline
tools at 32 CPU threads by default. Override the ceiling with `OPENREEF_MAX_CORES` only after
testing the selected worker hardware. The image disables OpenMVS's optional JPEG-XL path because
the pinned Ubuntu OpenCV does not expose its JPEG-XL write flag; JPG, PNG, TIFF, and WebP survey
inputs remain supported.

The final image installs the same COLMAP runtime libraries used by the Caspar builder and verifies
both dynamic-library resolution and `colmap --help` before publication. This prevents a compiled
Caspar binary from reaching RunPod with a builder-only shared-library dependency.

Caspar is the cloud default through `OPENREEF_BA_BACKEND=caspar`. For controlled mapper-only
comparisons, set `OPENREEF_BA_BACKEND=ceres` with `OPENREEF_CERES_USE_GPU=0` for the established CPU
baseline, or `OPENREEF_CERES_USE_GPU=1` for Ceres CUDA when using a COLMAP image linked to a
CUDA/cuDSS-enabled Ceres build. See [CASPAR_BENCHMARK.md](CASPAR_BENCHMARK.md) for the fixed A/B/C
protocol and limitations.

During each job, the browser status card reports the source transfer from Drive or SharePoint to
the worker: images completed, bytes copied, elapsed time, and average throughput. The final values
remain visible after reconstruction begins and are also written to the worker log and job record.
The same card times the complete run and each queue, reconstruction, and result-upload step so
successive datasets and worker types can be compared from the retained job record.

## Storage

Personal Google Drive is the initial provider. A new app-created folder uses the narrow `drive.file`
scope. Reusing a pre-existing dedicated folder such as `reefplot` requires Drive scope; the API and
worker remain rooted to that folder ID. The existing SharePoint adapter remains available for later
organisational deployment.
