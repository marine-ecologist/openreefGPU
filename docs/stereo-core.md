# Stereo surveys

Stereo is opt-in. Existing `openreef-pipeline DATASET` commands continue to use the
single-camera workflow. The native smoke test exercises both reconstruction paths.

## Calibrate once for each physical rig and optical setup

Supply a JSON intrinsics file with `schemaVersion: 1` and two `cameras`, in left,
right order. Each camera needs a stable `id`, calibrated pixel `width` and `height`,
a COLMAP `model` (`PINHOLE`, `OPENCV` or `OPENCV_FISHEYE`) and `params`.
`PINHOLE` parameters are `[fx, fy, cx, cy]`; `OPENCV` adds `[k1, k2, p1, p2]`.
The camera IDs identify physical cameras, rather than the names of GoPro files.
Use underwater intrinsics measured with the same housing, resolution, field of view
and lens settings as the survey. `OPENCV_FISHEYE` adds `[k1, k2, k3, k4]` to the four pinhole parameters.
Calibration supports two fisheye cameras or two pinhole/OpenCV cameras; mixed
fisheye and non-fisheye calibration is rejected.

Capture at least six synchronised, overlapping views of a chessboard in varied
poses. Keep the same corner origin/orientation visible in both sensors. Measure
the square size in metres. For example, a board with 9 by 6 inner corners:

```sh
python -m openreef.pipeline.stereo calibrate \
  --left calibration/gopro-left --right calibration/gopro-right \
  --profile intrinsics.json --columns 9 --rows 6 --square-size-m 0.025 \
  --output rig-profile.json
```

Calibration holds each camera's intrinsics fixed and estimates `rightFromLeft`:
`X_right = rotation @ X_left + translation`. Translation is in metres. Profiles
contain the board dimensions, pairing diagnostics, accepted/rejected detections
and stereo reprojection RMS. Calibration fails above 2 pixels RMS by default
(`--max-rms` changes this threshold). Review the result before reusing it; a low
RMS alone cannot establish that a physical calibration setup was correct.

## Pair and reconstruct a survey

```sh
python -m openreef.pipeline.stereo prepare \
  --left survey-left --right survey-right --profile rig-profile.json \
  --output paired-survey --max-delta-ms 30 --right-offset-ms 0
openreef-pipeline paired-survey --stereo --dense-compact
```

Preparation requires a fresh output dataset. It copies photographs to distinct
camera folders and assigns paired exposures identical frame filenames. Missed
exposures become single-sensor rig frames, without shifting subsequent pairs.
The originals stay in their source folders. Both sensors must use the same file
extension for a paired exposure and match their calibrated pixel dimensions.

Timing comes from EXIF DateTimeOriginal, SubSecTimeOriginal and OffsetTimeOriginal.
Clock correction is **added to the right timestamp**, in milliseconds. Missing or
duplicate capture timestamps fail rather than silently pairing by filename or mtime.
For extracted video frames or cameras without adequate EXIF timing, pass
`--timestamps timestamps.json` to either command. Its structure is
`{"left-camera-id": {"GOPR0001.jpg": 123.456}, "right-camera-id": {...}}`, using
seconds on a common time base. Every selected photograph needs an entry.
Camera clocks without EXIF timezones use a shared neutral epoch, independent of
host timezone. Correct differing camera clock offsets explicitly.

`stereo/` retains the reusable profile, rig configuration, source-to-frame manifest,
pairing diagnostics and matching diagnostics. Feature extraction uses separate
calibrated camera models. Rig configuration runs before matching. Mapping freezes
sensor poses, focal lengths, principal points and distortion parameters. Completion
markers prevent a partial extraction/matching run from being mistaken for a
completed stage; changing sources or calibration requires a new dataset.

The matcher builds explicit same-frame stereo edges and a temporal window across
both sensors (`--overlap`, default 10). Sparse long-range candidates add loop
closure opportunities (`--loop-interval`, default 50; 0 disables them;
`--loop-neighbors`, default 5). COLMAP performs GPU descriptor matching and geometric
verification for these candidates. This is a deterministic sparse candidate strategy,
not vocabulary-tree place recognition; loops outside its candidate windows can be
missed. Diagnostics report candidate/verified pairs, stereo inliers and isolated
images. No exhaustive all-image match is required for a large survey.

Prepared datasets are also automatically recognized by the desktop stage command builder.
After sparse reconstruction, the existing undistortion, MarkerTag, OpenMVS, mesh,
texture and optional OpenSplat stages consume the standard selected COLMAP model.
Viewer output formats are unchanged. OpenSplat remains an explicit optional stage.

## Reproduce the native regression check

```sh
PYTHONPATH=src python scripts/smoke-stereo.py \
  --output /tmp/new-stereo-smoke --colmap /path/to/colmap
```

This creates a textured synthetic 3D survey, deliberately misses one right-camera
exposure, reconstructs it with CPU COLMAP, checks all six frames and the fixed metric
baseline, and reconstructs the reference stream using the single-camera path.
It writes `smoke-report.json`. COLMAP must support native rig configuration and
`Mapper.ba_refine_sensor_from_rig` (validated locally with COLMAP 4.2.1).
