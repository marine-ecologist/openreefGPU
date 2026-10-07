# Caspar bundle-adjustment benchmark

OpenReef 0.6.4 makes the mapper bundle-adjustment backend explicit. openreefGPU 0.6.4-gpu.1
compiles COLMAP commit `68b722be23fdba964a24583720bde07675171d47` with
`CASPAR_ENABLED=ON` and uses Caspar by default on RunPod.

## Fixed comparison

Use the same source images, camera model, feature database, image size, sequence overlap, CPU
limit, GPU, and container image for every run. Delete only the sparse output between runs so the
feature extraction and matching inputs stay identical. Compare the **Sparse reconstruction** time,
not Drive transfer, queue/startup, output upload, or total job time.

| Run | Mapper configuration | Environment | Sparse time | Relative to A |
| --- | --- | --- | ---: | ---: |
| A | Ceres CPU baseline | `OPENREEF_BA_BACKEND=ceres`, `OPENREEF_CERES_USE_GPU=0` | 214 s prior baseline | 1.00× |
| B | Ceres CUDA | `OPENREEF_BA_BACKEND=ceres`, `OPENREEF_CERES_USE_GPU=1` | Pending compatible build | Pending |
| C | Caspar GPU | `OPENREEF_BA_BACKEND=caspar`, `OPENREEF_CERES_USE_GPU=0` | Pending 0.6.4 run | Pending |

The 214-second value is retained as the pre-Caspar baseline supplied for this experiment. Replace
it with a fresh A run if the image, host, feature database, or mapper inputs change.

## Controls and limitations

- Caspar is experimental and currently supports `SIMPLE_RADIAL` and `PINHOLE`; OpenReef rejects
  other camera models before starting sparse reconstruction.
- The worker selects both local and global incremental-mapper bundle adjustment with
  `--Mapper.ba_local_backend CASPAR` and `--Mapper.ba_global_backend CASPAR`.
- `OPENREEF_BA_GPU_INDEX=-1` lets COLMAP choose the CUDA device. Set a numeric device index only
  for a controlled multi-GPU host.
- Run B requires COLMAP to be linked to a CUDA/cuDSS-enabled Ceres build. The Caspar image proves
  Run C support at build time but does not claim Ceres CUDA support from Ubuntu's Ceres package.
- Record registered-image count and sparse-point count with each result. A faster run that
  reconstructs a different model is not a valid speed comparison.

After each run, add the sparse time and calculate `214 / sparse_time` for the speedup against A.
