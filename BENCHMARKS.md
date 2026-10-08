# OpenReef compute benchmarks

## OpenReef 0.6.4 Caspar cloud matrix

Run date: 8 October 2026. These runs used the same 248-image Reefplot source
set and compact reconstruction profile as the 0.6.3 matrix below. Every exact
run used immutable image `ghcr.io/marine-ecologist/openreef-gpu:0.6.4-gpu.4`,
one worker, one GPU, and a RunPod endpoint restricted to the named accelerator
subtype. The image was built with `CASPAR_ENABLED=ON`, and OpenReef selected the
Caspar bundle-adjustment backend for sparse reconstruction.

The comparable total is the same eight-stage compute measure used for 0.6.3.
It excludes queue time, Google Drive source staging, the cloud-only source
preparation step, and result upload. M2 and M5 results are deliberately excluded
from this cloud-GPU ranking because the macOS pipeline does not use CUDA/Caspar.

### Ranked compute-only result

| Rank | Accelerator | Job | Compute | Change vs 0.6.3 | RunPod rate | Compute-only cost |
| ---: | --- | --- | ---: | ---: | ---: | ---: |
| 1 | B200 180 GB Pro | [`0f687069`](https://marine-ecologist.github.io/openreefGPU/?job=0f687069-9540-4459-89e6-e5a0860c8f83) | **378 s** | 5.9% slower | $8.64/hr | $0.91 |
| 2 | RTX PRO 6000 96 GB | [`dc3fe7b8`](https://marine-ecologist.github.io/openreefGPU/?job=dc3fe7b8-dcc4-4976-8a9e-8fde1fb91062) | **419 s** | 22.2% slower | $3.49/hr | $0.41 |
| 3 | L40S 48 GB Pro | [`5fc09712`](https://marine-ecologist.github.io/openreefGPU/?job=5fc09712-7b80-4c72-a067-7d9366591de3) | **433 s** | **34.2% faster** | $1.75/hr | $0.21 |
| 4 | H200 SXM 141 GB | [`fd9fdba8`](https://marine-ecologist.github.io/openreefGPU/?job=fd9fdba8-031b-413e-9b4a-dad5e686add2) | **452 s** | **6.4% faster** | $5.93/hr | $0.74 |
| 5 | RTX 5090 32 GB Pro | [`7ce29e69`](https://marine-ecologist.github.io/openreefGPU/?job=7ce29e69-f2bd-4684-a918-82e4e7219e0b) | **471 s** | 5.4% slower | $1.58/hr | $0.21 |
| 6 | L4 24 GB | [`5736a507`](https://marine-ecologist.github.io/openreefGPU/?job=5736a507-27d7-4510-ac3b-9a1536985a64) | **546 s** | **15.7% faster** | $0.69/hr | **$0.10** |
| 7 | H100 SXM 80 GB Pro | [`931932a7`](https://marine-ecologist.github.io/openreefGPU/?job=931932a7-aad6-42ce-bb37-8813d00f0152) | **572 s** | 19.9% slower | $4.79/hr | $0.76 |
| 8 | A40 48 GB | [`335d0fe1`](https://marine-ecologist.github.io/openreefGPU/?job=335d0fe1-9395-4d17-8442-48b8500004a8) | **629 s** | 9.0% slower | $1.22/hr | $0.21 |
| 9 | B300 288 GB Pro | [`126c4028`](https://marine-ecologist.github.io/openreefGPU/?job=126c4028-c9f0-4285-8c74-5266775154b3) | **718 s** | 34.7% slower | $10.65/hr | $2.12 |
| — | RTX 4090 24 GB Pro | [`48bd21c4`](https://marine-ecologist.github.io/openreefGPU/?job=48bd21c4-5a4f-4cc7-84c7-d0de24f14c50) | failed | n/a | $1.10/hr | n/a |

B200 was fastest at 378 seconds. L4 was cheapest for compute at about $0.10,
while L40S provided the strongest practical speed/cost balance: 433 seconds for
about $0.21 of compute. Only L40S, L4, and H200 improved on their 0.6.3 totals.

### Stage breakdown

| Accelerator | Feature | Match | Sparse | Undistort | Import | Dense | Surface | Texture | Total |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| B200 180 GB Pro | 10 | 17 | 232 | 2 | 3 | 93 | 2 | 19 | **378** |
| RTX PRO 6000 96 GB | 11 | 17 | 246 | 2 | 2 | 117 | 2 | 22 | **419** |
| L40S 48 GB Pro | 17 | 23 | 249 | 3 | 2 | 117 | 2 | 20 | **433** |
| H200 SXM 141 GB | 15 | 23 | 258 | 2 | 1 | 127 | 2 | 24 | **452** |
| RTX 5090 32 GB Pro | 15 | 23 | 279 | 5 | 3 | 122 | 3 | 21 | **471** |
| L4 24 GB | 19 | 28 | 314 | 3 | 3 | 151 | 3 | 25 | **546** |
| H100 SXM 80 GB Pro | 22 | 33 | 322 | 3 | 2 | 159 | 3 | 28 | **572** |
| A40 48 GB | 18 | 34 | 369 | 3 | 2 | 165 | 2 | 36 | **629** |
| B300 288 GB Pro | 18 | 23 | 499 | 3 | 2 | 145 | 3 | 25 | **718** |

All values are seconds. The successful jobs reported OpenReef `0.6.4` and
openreefGPU `0.6.4-gpu.4` and produced viewer-ready GLBs. Recorded GLB sizes
ranged from 56,877,732 bytes (RTX 5090) to 60,317,896 bytes (L4).

### Transfer and cost record

| Accelerator | Queue | Drive staging | Upload | Estimated billed worker time | Estimated job cost |
| --- | ---: | ---: | ---: | ---: | ---: |
| B200 | 214.9 s | 81 s | 15 s | 476 s | $1.14 |
| RTX PRO 6000 | 223.1 s | 21 s | 9 s | 451 s | $0.44 |
| L40S | 10.9 s | 45 s | 20 s | 500 s | $0.24 |
| H200 | 228.8 s | 24 s | 10 s | 488 s | $0.80 |
| RTX 5090 | 196.7 s | 743 s | 19 s | 1,236 s | $0.54 |
| L4 | 165.2 s | 761 s | 17 s | 1,328 s | $0.25 |
| H100 | 148.6 s | 714 s | 17 s | 1,306 s | $1.74 |
| A40 | 242.2 s | 34 s | 16 s | 681 s | $0.23 |
| B300 | 158.7 s | 33 s | 12 s | 766 s | $2.27 |
| **Bottom line** | queue excluded | transfer recorded separately | — | **7,232 s** | **$7.66 estimated** |

The nine successful exact runs used approximately **$5.68 of compute-only
time**. Estimated worker cost includes source staging, source preparation,
compute, and result upload, but excludes queue time and browser-side job
preparation. Across the full exercise—including two provisional runs, the first
RTX 5090 failure, the RTX 4090 failure, and platform overhead—the RunPod balance
moved from $21.13 to $11.84: an observed decrease of **$9.29**, within the
authorised $20 budget.

### Interpretation and failures

Caspar does not accelerate the pipeline uniformly. It affects bundle adjustment
inside sparse reconstruction; feature extraction, matching, OpenMVS dense
reconstruction, meshing, and texturing use different CPU/GPU paths. RunPod GPU
tiers also arrive with different host CPUs, and the worker caps native tools at
32 threads. For this medium-sized reconstruction, fixed setup costs and host-CPU
variation can outweigh Caspar's gain. One run per accelerator is enough for a
practical end-to-end comparison, but not enough to attribute every difference
causally to the GPU or Caspar.

The exact RTX 4090 run failed during feature extraction after two seconds with
native exit code 250/SIGABRT, before sparse reconstruction or Caspar ran. The
first exact RTX 5090 attempt failed at the same stage after three seconds; the
single approved retry completed successfully. These failures therefore do not
show a Caspar bundle-adjustment fault.

## Reefplot 248-image survey

Run dates: 6–7 October 2026. All runs used OpenReef 0.6.3 with the compact cloud
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

Among the initial baseline runs, the RTX 5090 was the fastest cloud option. The H100 was
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

## Six-GPU expansion

Six additional exact-GPU runs were completed on 7 October 2026. RTX PRO 4500
was not exposed by the available 32 GB tier, so an exact L40S was used as the
approved mid-tier substitute.

### Ranked compute-only cloud GPU result

| Rank | Accelerator | Comparable compute | RunPod rate | Compute-only cost |
| ---: | --- | ---: | ---: | ---: |
| 1 | RTX PRO 6000 96 GB | **343 s** | $3.49/hr | $0.33 |
| 2 | B200 180 GB | **357 s** | $8.64/hr | $0.86 |
| 3 | RTX 4090 24 GB | **428 s** | $1.10/hr | $0.13 |
| 4 | RTX 5090 32 GB Pro | **447 s** | $1.58/hr | $0.20 |
| 5 | H100 80 GB Pro | **477 s** | $4.79/hr | $0.63 |
| 6 | H200 141 GB | **483 s** | $5.93/hr | $0.80 |
| 7 | B300 288 GB | **533 s** | $10.65/hr | $1.58 |
| 8 | A40 48 GB | **577 s** | $1.22/hr | $0.20 |
| 9 | L4 24 GB | **648 s** | $0.69/hr | $0.12 |
| 10 | L40S 48 GB | **658 s** | $1.75/hr | $0.32 |

The RTX PRO 6000 was fastest overall. The RTX 4090 was the best value among
the six new tests: only 71 seconds slower than B200 but about one sixth of
B200's compute-only cost. B300 was slower than B200, RTX PRO 6000, RTX 4090,
RTX 5090, H100, and H200 despite having the largest GPU and highest hourly rate.

This is a mixed CPU/GPU pipeline, not an isolated GPU test. Sparse
reconstruction dominated several runs and varied with the worker's CPU. For
example, sparse reconstruction took 215 seconds on B200, 311 seconds on B300,
and 401 seconds on L40S. Higher GPU tiers therefore do not guarantee a faster
or cheaper OpenReef job.

### New-run stage breakdown

| Accelerator | Feature | Match | Sparse | Undistort | Import | Dense | Surface | Texture | Total |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| RTX PRO 6000 96 GB | 9 | 17 | 214 | 2 | 3 | 81 | 3 | 14 | **343** |
| B200 180 GB | 10 | 16 | 215 | 2 | 2 | 92 | 2 | 18 | **357** |
| RTX 4090 24 GB | 15 | 21 | 253 | 4 | 3 | 107 | 3 | 22 | **428** |
| H200 141 GB | 16 | 27 | 280 | 2 | 3 | 129 | 3 | 23 | **483** |
| B300 288 GB | 17 | 23 | 311 | 2 | 2 | 150 | 3 | 25 | **533** |
| L40S 48 GB | 22 | 29 | 401 | 3 | 2 | 167 | 2 | 32 | **658** |

All values are seconds. The M2 Max completed sparse reconstruction faster than
every cloud worker, but cloud GPUs produced the dense cloud and textured mesh
far faster.

### Transfer and cost record

| GPU | Job | Queue | Drive staging | Transfer rate | Upload | Estimated billed worker time | Estimated job cost |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| RTX 4090 | [`5ab16a90`](https://marine-ecologist.github.io/openreefGPU/?job=5ab16a90-67f6-4fe0-bf22-94df183dab53) | 208.4 s | 945 s | 0.27 MB/s | 14 s | 1,389 s | $0.42 |
| RTX PRO 6000 | [`aec80780`](https://marine-ecologist.github.io/openreefGPU/?job=aec80780-3aab-4b2d-94b1-64b6c5dd1cda) | 90.5 s | 38 s | 7.75 MB/s | 15 s | 398 s | $0.39 |
| H200 | [`64314ce3`](https://marine-ecologist.github.io/openreefGPU/?job=64314ce3-cfe4-4635-87d7-b8b55a3fdbbf) | 10.3 s | 695 s | 0.37 MB/s | 17 s | 1,198 s | $1.97 |
| B200 | [`bf703c10`](https://marine-ecologist.github.io/openreefGPU/?job=bf703c10-9ee3-4e8e-9bed-ee35d77928cd) | 70.8 s | 42 s | 7.11 MB/s | 17 s | 418 s | $1.00 |
| B300 | [`7282e832`](https://marine-ecologist.github.io/openreefGPU/?job=7282e832-b075-44bf-a080-4fd528a62727) | 251.5 s | 36 s | 8.22 MB/s | 13 s | 584 s | $1.73 |
| L40S | [`565a6c2b`](https://marine-ecologist.github.io/openreefGPU/?job=565a6c2b-b673-42d2-a8f2-c7db9f3a5129) | 127.8 s | 31 s | 9.68 MB/s | 12 s | 703 s | $0.34 |
| **Bottom line** | six completed jobs | queue excluded | transfer recorded separately | — | — | **3,690 s** | **$5.86 estimated** |

Estimated billed worker time is source staging + cloud source preparation +
the eight compute stages + result upload. Queue and browser-side preparation
are excluded. The six runs used approximately **$4.01 of compute-only time**.
The RunPod balance moved from the authorised $7.20 budget to $1.15, an observed
decrease of approximately **$6.05** including worker startup and runtime
overhead not represented by the broker's stage timers. The test therefore
finished **$1.15 under budget**.

Drive transfer performance varied from 0.27 to 9.68 MB/s. This variation is why
the main ranking excludes transfer. Worker version `0.6.3-gpu.15` downloads up
to eight Drive files concurrently; the RTX 4090 run used `0.6.3-gpu.14`, whose
compute pipeline and settings were identical but whose source download was
serial. A network-volume cache remains preferable for repeated tests in one
compatible RunPod region.

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
settings, raw stage times, and the comparable total.

### M5 MacBook Air 24 GB handoff

The prepared archive is `~/Desktop/openreef-m5-air-benchmark.zip` (240,059,812
bytes), SHA-256:

```text
26b73059084f3e5c4813bd6555e7bd68651bc6039577d969fdb491d919cec065
```

It contains the identical 248-image dataset, checksum manifest, benchmark
script, README, and a double-click `run-macos-benchmark.command` launcher. Copy
the archive to the M5, extract it, and run the launcher after installing the
normal OpenReef desktop prerequisites. The result JSON includes the Mac model,
chip, RAM, CPU count, all stage times, and the comparable compute total.
