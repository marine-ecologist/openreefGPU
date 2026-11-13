# Stereo validation — 13 November 2026

Implementation is in the sibling fresh checkouts `openreef-stereo` and
`openreefGPU-stereo`. The pre-existing `openreefGPU` workspace is unchanged.
The fresh GPU checkout carries forward the workspace's newer Gaussian-viewer and
diagnostic/setup changes, which were absent from the fetched GitHub snapshot.

Passed checks:

- Core stereo/CLI suite: **19 tests**.
- Complete GPU Python suite: **38 tests** (two existing setup tarfile deprecation warnings).
- Broker/JavaScript suite: **23 tests**.
- Frontend and broker TypeScript checks.
- Production frontend build (`npm run build`).
- GPU version-contract check: **0.6.5-gpu.10**, pinned core **0.6.5**.
- Packaged core patch applies to the pinned original sources and reproduces every
  tested runtime source file byte for byte.

Calibration and worker tests use Python 3.12 with the worker's exact image-library
versions: NumPy 2.2.6, Pillow 11.3.0 and OpenCV headless 4.12.0.88. Calibration tests
recover known rotation and translation from projected chessboards, including both
pinhole and fisheye projection models. Timing tests cover clock offsets, EXIF
subseconds/timezones, filename collisions, dropped exposures and ambiguous timing.
Fault injection verifies that partial extraction does not produce a completion marker.

A real **COLMAP 4.2.1 CPU** smoke reconstruction generated six synthetic rig frames
and deliberately omitted one right-camera exposure:

| Check | Result |
| --- | --- |
| Stereo registered images | 11 of 11 |
| Stereo rig frames | 6 of 6 |
| Reconstructed stereo points | 344 |
| Rig baseline | 0.15 m, unchanged by mapping |
| Verified stereo pairs | 5 of 5 |
| Images without verified matches | 0 |
| Single-camera registered images | 6 of 6 |
| Single-camera points | 127 |

The same stereo model successfully completed native COLMAP undistortion. The
existing OpenSplat input preparation resolved its `images` and `sparse/0` links
and retained all 11 registered images. Standard sparse/dense handoff formats are
preserved for downstream OpenMVS and viewer stages.

Limits of this verification:

- No Docker executable or live RunPod plugin tools were exposed in this chat.
  The GPU image has not been built or deployed, and no live GPU reconstruction
  was submitted. The broker health endpoint was readable; full endpoint image,
  hardware and environment settings were not available.
- Native validation used synthetic images and CPU COLMAP, rather than underwater
  GoPro photographs or the exact Linux worker build.
- Actual OpenMVS densification and OpenSplat training were not run.
- The core Qt/VTK desktop UI test suite was not run; validation covered the core
  stereo/CLI tests, native commands, GPU tests and cloud frontend build.

Run the documented worker-image build and a small calibrated survey on RunPod
before rolling out stereo to production. Existing active workers remain unchanged.
