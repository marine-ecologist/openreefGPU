# Direct network-volume serverless runs

This worker input bypasses the Cloudflare broker, Google Drive, SharePoint, uploads,
cloud job metadata, and source cache population. It authenticates with the RunPod
endpoint's API key. The existing browser/Drive workflow is unchanged.

## Deploy once

Build and publish the changed worker image from this repository root. The fast
release Dockerfile reuses the pinned native CUDA/COLMAP/OpenMVS stack:

```bash
docker buildx build --platform linux/amd64 \
  -f worker/Dockerfile.release \
  -t ghcr.io/marine-ecologist/openreef-gpu:0.6.5-gpu.10-volume.2 \
  --push .
```

In RunPod, configure your OpenReefGPU serverless endpoint to use that image and attach
network volume `s8baqr2m12` (`openreef-serverless-cache`, US-NE-1).
Keep minimum workers at zero. Set the endpoint execution timeout high enough for
reconstruction; worker termination/timeouts cannot be caught by Python.
An existing worker must be replaced by the updated image before this input works.
Both Dockerfiles copy the new worker module; the stereo patch also adds optional
source links while keeping the existing Drive copy behavior as the default.

## Submit the uploaded BoobyPass folders

Set `RUNPOD_API_KEY` in your terminal environment using your regular **RunPod API key**,
not the S3 secret. The helper never prints the key. From this repository:

```bash
python3 scripts/submit-volume.py --endpoint 93jkogjuc9l6pu \
  --folder original/video_video_A-BoobyPass-12-11-26 \
  --count 200 --max-image-size 1600 --cores 16 --wait
```

`93jkogjuc9l6pu` is the endpoint recorded in this repository; substitute your deployed
endpoint ID if different. Omit `--count` to use the whole folder. `--start 200 --count
200` selects the next consecutive block in filename order. Partial pipeline experiments
can use `--stages features,matching,sparse`; use stage names accepted by the pinned
OpenReef CLI. Each request starts a fresh dataset; partial runs do not resume earlier runs.

To run A and B **independently**, repeat `--folder`:

```bash
python3 scripts/submit-volume.py --endpoint 93jkogjuc9l6pu \
  --folder original/video_video_A-BoobyPass-12-11-26 \
  --folder original/video_video_B-BoobyPass-12-11-26 \
  --max-image-size 1600 --cores 16 --wait
```

Both jobs are submitted before polling starts. Stop polling with Ctrl+C without
cancelling the remote jobs. RunPod job IDs are printed immediately; query them later
through RunPod's `/status/<id>` API or console. Failed/cancelled/timed-out jobs cause
the helper to exit unsuccessfully when `--wait` is used.

## Calibrated A+B stereo

Upload the real calibration and timing JSON into `openreef-sources`, for example
`openreef-sources/calibration/rig.json` and `openreef-sources/calibration/times.json`.
Then:

```bash
python3 scripts/submit-volume.py --endpoint 93jkogjuc9l6pu \
  --folder original/video_video_A-BoobyPass-12-11-26 \
  --right-folder original/video_video_B-BoobyPass-12-11-26 \
  --profile calibration/rig.json --timestamps calibration/times.json \
  --max-image-size 1600 --cores 16 --wait
```

The profile follows OpenReef's existing stereo schema (camera intrinsics, image
size, metric baseline and relative rotation). Timing JSON maps each profile camera
ID to `{ "frame_000001.jpg": seconds, ... }`. Use actual frame times and synchronization;
filename order alone is not a stereo calibration. `--timestamps` can be omitted only
when photographs contain sufficiently precise capture EXIF timestamps. Stereo frame
preparation links to original files and retains separate camera identities even when
filenames collide. Mono subset options are rejected for stereo.

## Results and persistence

Source files stay at `/runpod-volume/openreef-sources/...`. Each invocation gets its
own UUID directory at `/runpod-volume/openreef-runs/<uuid>/`, containing:

- `job.json`: durable status, versions, source, pipeline command, and result paths.
- `source-manifest.json`: selected source names/paths, sizes and modification times.
- `pipeline.log`: flushed pipeline output.
- `dataset/`: source links and all generated intermediates and outputs.

The RunPod result contains `dataset`, `statusPath`, `logPath`, and `artifacts` paths.
For example `/runpod-volume/openreef-runs/<uuid>/job.json` is available through S3 as
`s3://s8baqr2m12/openreef-runs/<uuid>/job.json`. No result upload to Drive occurs.
The browser viewer is not connected to these runs yet; download the resulting GLB
using the existing RunPod S3 profile:

```bash
aws --profile runpod --region us-ne-1 \
  --endpoint-url https://s3api-us-ne-1.runpod.io \
  s3 cp 's3://s8baqr2m12/openreef-runs/<uuid>/dataset/openmvs/scene_mesh_compact_textured.glb' ./model.glb
```

Use the actual returned artifact path if the model is under `dataset/models` instead.
Runs never overwrite earlier runs or delete sources. Logs and intermediate files also
remain after ordinary processing failures. Hard worker termination may leave `job.json`
marked running; consult RunPod job status in that case. Inputs are linked rather than
snapshotted: leave source files unchanged during a run. Retained dense results can use
substantial storage; remove selected old run directories when no longer needed.

## Raw RunPod request

POST to `https://api.runpod.ai/v2/<endpoint>/run` with a Bearer API key and this body:

```json
{
  "input": {
    "schema_version": 1,
    "openreef_version": "0.6.5",
    "openreef_gpu_version": "0.6.5-gpu.10",
    "volume": {
      "folder": "original/video_video_A-BoobyPass-12-11-26",
      "count": 200
    },
    "pipeline": {"max_image_size": 1600, "cores": 16}
  }
}
```

The versions must match the deployed image. Volume paths are relative to
`openreef-sources`; absolute paths, traversal outside that root, and source links
outside it are rejected. Pipeline overrides are restricted to validated `cores`,
`max_image_size`, `max_resolution`, `sequential_overlap`, and `stages` values.
