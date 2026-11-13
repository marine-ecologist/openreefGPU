# OpenReefGPU stereo integration

Use **Stereo camera folders** in the cloud job form. Select the left and right
folders inside the configured Google Drive reefplot root, load the rig profile JSON,
and set the pair tolerance and correction added to the right camera clock.
The photograph limit applies to each camera; the combined job must still fit the
existing 2,000-image and 50 GiB limits. Cameras may contain identical GoPro filenames.
File IDs and camera identities remain separate throughout staging and submission.
The existing upload and single-folder modes remain available for monocular jobs.

Calibrate the rig and its per-camera intrinsics before submitting a survey. The
core checkout's [stereo guide](docs/stereo-core.md) documents calibration
and timestamp requirements. Calibration runs with
`python -m openreef.pipeline.stereo calibrate`; the cloud form accepts the saved
metric profile. Local directories and explicit per-frame timestamp JSON are
supported by the core CLI. The cloud API also accepts explicit timestamps; the
form currently reads image EXIF timing.

The job API accepts an optional `stereo` object:

```json
{
  "name": "Paired reef survey",
  "maxFiles": 100,
  "stereo": {
    "leftFolder": "gopro-left",
    "rightFolder": "gopro-right",
    "profile": {"schemaVersion": 1, "cameras": ["replace with calibrated camera objects"]},
    "maxDeltaMs": 30,
    "rightOffsetMs": 0,
    "overlap": 10,
    "loopInterval": 50,
    "loopNeighbors": 5
  }
}
```

The illustrative `profile` above must be replaced by the complete calibration JSON,
including metric extrinsics. Stereo is mutually exclusive with `sourceFolder` and
browser-upload `files`. The broker validates profile geometry and source limits
before writing the job. The worker repeats profile validation and independently
stages both cameras, pairs exposures and runs the core pipeline with `--stereo`.
Missing exposures remain singleton rig frames. Missing/ambiguous timing, changed
resolution and unsupported calibration models fail explicitly.

Completed jobs expose pairing diagnostics, the reusable profile and verified-match
diagnostics as download links. Full-project archives retain all stereo manifests
and the `--stereo` resume command alongside existing COLMAP/OpenMVS outputs.
With source images excluded, use `stereo/frames.json` to restore the original photos
to their renamed paths under `images/` before resuming.

## Worker packaging and rollout

The fresh GPU checkout retains local Gaussian viewer/output changes from the
existing workspace. Version **0.6.5-gpu.10** separates the stereo-capable worker
from older workers. Both broker and worker enforce that version contract.

The worker applies `worker/patches/openreef-stereo.patch` to the pinned OpenReef
0.6.5 source before installation. This makes the build independent of unpublished
core commits. The patch contains the runtime core changes and CLI entry point.
To regenerate after editing the sibling core checkout:

```sh
python scripts/package-stereo-patch.py ../openreef-stereo
node scripts/check-versions.mjs
```

For the stereo-only release, dispatch the existing GPU Docker workflow with
`reuse_native_image=true`. `worker/Dockerfile.release` reuses the immutable deployed
GPU image and verifies its native dependency contract before adding the stereo
module. Default builds still use `worker/Dockerfile` to rebuild the entire stack.
The fast build is deliberately restricted to this stereo version and compatible
base; use a full build for later native/core upgrades.

Build with the existing GPU Docker workflow. The build checks for the rig
configurator and fixed-sensor-pose mapper flag, and verifies the stereo module
imports. Review/regenerate the patch when upgrading the pinned upstream release;
patch application fails the build if it no longer applies cleanly.

Deploy the new worker image and matching broker/frontend together, then run a small
known-rig survey on an endpoint. The `.10` worker, API and frontend have now been deployed, and all three listed
RunPod workers are on release 58. End-to-end OpenMVS/OpenSplat/GPU behavior still
requires the user's test survey on the built worker image. See
[STEREO_DEPLOYMENT.md](STEREO_DEPLOYMENT.md) for the deployment record.

## Verification

```sh
PYTHONPATH=.:../openreef-stereo/src python -m pytest
node --test tests/*.mjs
npm run check:versions
npx tsc --noEmit
npx tsc --noEmit -p cloud/api/tsconfig.json
```

The core native smoke script checks a real CPU COLMAP reconstruction, including a
missed exposure, fixed metric baseline and single-camera regression. Its generated
images are synthetic; they are not a substitute for an underwater calibration and
survey test.

Production deployment status is recorded in
[STEREO_DEPLOYMENT.md](STEREO_DEPLOYMENT.md). The RunPod console verified that all
three workers reached release 58 automatically. No real test survey was submitted.
