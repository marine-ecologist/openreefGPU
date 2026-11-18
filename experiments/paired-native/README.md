# Temporary native paired-video comparison

This is an isolated COLMAP/Ceres/OpenMVS experiment, not a change to the production
OpenReef pipeline. It uses the supplied `stereo_pairs/<site>/A|B` JPEGs and
`pairs.csv`, without re-extracting frames or reusing earlier audio synchronization.
The current visual check starts with BoobyPass, the site of the curved GLB.
Every supplied pair is used by default. Metashape quality scores are optional;
no scores or solved Metashape geometry are required.

The comparison uses independent camera poses and two shared fisheye sensors.
Initial focal lengths are 4218.7204 and 3049.6774 pixels, derived from the supplied
123.6-degree air diagonal FOV and flat-port water index 1.333. There is no fixed
A/B rotational transform and no rolling-shutter solver. Full-resolution SIFT
uses 40k keypoints, vocabulary-tree generic retrieval and stationary-point
filtering. Native match/tie limits and cleanup metrics differ from Metashape;
`experiment.json` and `comparison.json` document these differences.

`paired-baseline-ba` performs actual joint bundle adjustment of image poses,
3D points, and f/cx/cy/k1/k2/k3/p1/p2 using Ceres. Each registered camera pair
contributes `(distance - 0.351 m) / 0.003 m`. These factors constrain distance
only. A single pose fixes the global coordinate gauge. Before the scale-bar
phase, only one gauge distance fixes overall scale; other pair baselines remain
free. The native projection is COLMAP THIN_PRISM_FISHEYE with a shared fx=fy and
k4/sx/sy held at zero, not a claim that all Metashape fisheye conventions match.

Cleanup removes at most 20% of points above the native ray-information
anisotropy threshold 15, then at most 10% above raw pixel RMS 0.3, with fitting
between passes. Those two metrics are explicitly not Metashape's proprietary
uncertainty/weighted reprojection measures. Scale-bar QC stops if median
absolute relative deviation exceeds 2.5% or over 5% of pairs exceed 10% deviation.
A few remaining outlier pairs are disabled in the result, not deleted from the
source folder. The model is then scaled to the median measured baseline and
rotated into the reef plane, with Z toward the cameras. It is not flattened or
assigned a false true-vertical/geographic reference.

Optional products use OpenReef/OpenMVS High-equivalent half-resolution depth,
original-resolution mesh and textures, a GLB, and a native mesh-based plan-view
orthographic PNG. Metashape's Mild filtering, .tls/Cesium LOD tiles, DEM-based
refined mosaic, and BigTIFF export are not equivalent features of this experiment.
Dense products require passing baseline QC and at least 95% coverage in the
largest connected component; incomplete source components are retained for
review rather than silently called a full-site mosaic.

## Build and test locally

```sh
cmake -S experiments/paired-native -B /tmp/openreef-paired-build -DCMAKE_BUILD_TYPE=Release
cmake --build /tmp/openreef-paired-build --parallel 2
PAIRED_BA_BINARY=/tmp/openreef-paired-build/paired-baseline-ba \
  python3 -m pytest experiments/paired-native/test_geometry.py -q
```

The tests compare projection to OpenCV, verify coordinate changes preserve
reprojection and distances, and run native Ceres fitting with incorrect initial
baselines and independently rotating cameras. The serverless Docker build runs
these tests again against the Linux native dependencies before publishing.

## Run on RunPod

`scripts/submit-paired-check.py --source <permanent-relative-folder> --wait`
creates a separate temporary endpoint using the existing endpoint's GPU pool,
one worker maximum, zero idle workers, the same network volume, and a bounded
execution timeout. It never modifies the original endpoint. Results and logs
remain on the volume; the temporary endpoint is removed after its completed
result has been saved locally. `RUNPOD_API_KEY` is read only from the terminal
environment and never printed. `--pair-limit N` selects an explicitly reported
continuous initial check window; it never silently subsamples stations.

The temporary worker requests 500 GB of container disk for recomputable dense
intermediates. Source images, sparse checkpoints, reports, and logs stay on the
network volume. Completed GLB, mesh PLY and orthographic PNG/coordinates are
copied back to the network volume with MD5 verification before success is
reported. Intermediate `.mvs` projects referring to temporary images are not
presented as reusable final artifacts.

The uploaded and checksum-verified first comparison can be submitted with:

```sh
python3 scripts/submit-paired-check.py \
  --source paired-native-check/BoobyPass-506d3ac4dab6 --wait
```

Source folders and all earlier runs remain unchanged. All 3 sites are supported
by the same input structure; only the BoobyPass comparison is prepared initially.
