# OpenReef compute benchmarks

## Reefplot 248-image survey

Run date: 6 October 2026. All runs used OpenReef 0.6.3 with the compact cloud
profile: maximum source image size 3200 px, dense resolution level 1 capped at
2560 px, 4096 px textures, sharpness weight 0, and seam levelling disabled.
The source set contains 248 JPEGs (257,557,509 bytes); the selected sparse
reconstruction contains 156 calibrated images.

The table reports compute seconds for the eight stage names shared by the local
and cloud pipelines. Queue time, cold start, source transfer/staging, result
upload, and the cloud-only 2–4 second `Preparing source images` step are excluded.
Lower is better.

| Stage | L4 24 GB | RTX 5090 32 GB Pro | A40 48 GB | H100 80 GB Pro | M2 Max 64 GB |
| --- | ---: | ---: | ---: | ---: | ---: |
| Feature extraction | 23 | 15 | 19 | 19 | 171.411 |
| Sequential matching | 31 | 23 | 35 | 27 | 57.999 |
| Sparse reconstruction | 385 | 264 | 325 | 257 | 148.191 |
| Undistort / PINHOLE | 3 | 3 | 3 | 4 | 3.620 |
| OpenMVS import | 3 | 2 | 2 | 2 | 0.690 |
| Dense point cloud | 170 | 115 | 164 | 139 | 835.821 |
| Surface mesh | 3 | 3 | 3 | 2 | 6.634 |
| Texture mesh | 30 | 22 | 26 | 27 | 213.879 |
| **Comparable compute total** | **648** | **447** | **577** | **477** | **1,438.245** |

The RTX 5090 was the fastest cloud option for this workload. The H100 was
3.02× faster than the M2 Max overall, but the M2 completed sparse reconstruction
faster. The large H100 gains came from feature extraction (9.02×), dense point
cloud generation (6.01×), and texturing (7.92×). The M2 pipeline took 1,438.378
seconds wall-clock from its first stage through final GLB export and produced an
88.8 MB compact GLB.

The successful H100 job is
[`680b4c21-3103-4e1b-92b7-5eafaa7fb203`](https://marine-ecologist.github.io/openreefGPU/?job=680b4c21-3103-4e1b-92b7-5eafaa7fb203).
Its network-volume cache produced 248 hits and zero Google Drive downloads:
source staging took 46 seconds (40.9 seconds inside the transfer callback), the
nine cloud compute steps including `Preparing source images` took 479 seconds,
result upload took 14 seconds, and the complete browser job took 578.2 seconds.
RunPod reported 9 minutes 3 seconds of worker execution, approximately $0.72 at
$4.79/hour; the 479-second compute-only portion was approximately $0.64.

### Validity and repeatability

The reconstruction settings and OpenReef source version are matched, but the
native toolchains are not byte-identical. Cloud runs use Linux x86-64 CUDA builds;
the M2 uses COLMAP 4.2.1 without GPU acceleration and OpenMVS 2.3.0 on arm64 macOS.
This is a valid end-user elapsed-compute comparison, not an isolated GPU kernel
benchmark. Keep those toolchain differences with any published interpretation.

The local command was:

```sh
python3 scripts/benchmark-local.py /path/to/clean/dataset \
  --openreef-dir /path/to/openreef \
  --cores 11
```

The helper combines local MarkerTags time with sparse reconstruction, ignores
nested OpenMVS progress labels, and writes a JSON record containing host,
settings, raw stage times, and the comparable total. Use the same command and
dataset for the planned M5 24 GB run.
