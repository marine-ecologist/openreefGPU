# syntax=docker/dockerfile:1.7
# openreefGPU owns the cloud adapter. The reconstruction pipeline itself is
# installed from one immutable OpenReef desktop commit declared in versions.json.
FROM nvidia/cuda:12.9.1-devel-ubuntu24.04 AS openmvs-builder

ARG DEBIAN_FRONTEND=noninteractive
ARG OPENMVS_VERSION=v2.3.0
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake git ca-certificates \
    libboost-filesystem-dev libboost-graph-dev libboost-iostreams-dev \
    libboost-program-options-dev libboost-serialization-dev libboost-system-dev \
    libceres-dev libcgal-dev libeigen3-dev libglfw3-dev libjpeg-dev \
    libopencv-dev libpng-dev libtiff-dev \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --branch "${OPENMVS_VERSION}" --depth 1 --recurse-submodules \
    https://github.com/cdcseacave/openMVS.git /opt/openMVS \
    && cmake -S /opt/openMVS -B /opt/openMVS/build \
      -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES="75;86;89" \
      -DOpenMVS_USE_CUDA=ON -DOpenMVS_BUILD_TOOLS=ON \
    && cmake --build /opt/openMVS/build --parallel "$(nproc)" \
    && cmake --install /opt/openMVS/build --prefix /opt/openmvs-install

FROM colmap/colmap:20260907.8077
ARG DEBIAN_FRONTEND=noninteractive
ARG OPENREEF_REPOSITORY=https://github.com/marine-ecologist/openreef.git
ARG OPENREEF_VERSION=0.6.3
ARG OPENREEF_REF=c5da966804860b500a4f5657c783d9df9de9cbfc
ARG OPENREEF_GPU_VERSION=0.6.3-gpu.1

RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates python3 python3-pip \
    libboost-iostreams-dev libboost-program-options-dev \
    libceres-dev libglfw3 libopencv-dev \
    && rm -rf /var/lib/apt/lists/*
COPY --from=openmvs-builder /opt/openmvs-install/ /usr/local/

RUN git init /opt/openreef \
    && git -C /opt/openreef remote add origin "${OPENREEF_REPOSITORY}" \
    && git -C /opt/openreef fetch --depth 1 origin "${OPENREEF_REF}" \
    && git -C /opt/openreef checkout --detach FETCH_HEAD \
    && test "$(git -C /opt/openreef rev-parse HEAD)" = "${OPENREEF_REF}"
RUN python3 -m pip install --break-system-packages --no-cache-dir --no-deps /opt/openreef \
    && python3 -m pip install --break-system-packages --no-cache-dir \
      runpod==1.7.13 numpy==2.3.3 Pillow==11.3.0 opencv-python-headless==4.12.0.88 \
    && test "$(python3 -c 'import openreef; print(openreef.__version__)')" = "${OPENREEF_VERSION}"

COPY worker/ /opt/openreefgpu/worker/

ENV PYTHONUNBUFFERED=1 \
    STORAGE_PROVIDER=google-drive \
    OPENREEF_VERSION=${OPENREEF_VERSION} \
    OPENREEF_GPU_VERSION=${OPENREEF_GPU_VERSION} \
    OPENREEF_MAX_IMAGE_SIZE=3200 \
    OPENREEF_MAX_RESOLUTION=2560 \
    OPENREEF_MAX_TEXTURE_SIZE=4096 \
    OPENREEF_TEXTURE_RESOLUTION_LEVEL=1 \
    GOOGLE_DRIVE_JOB_ROOT=jobs

WORKDIR /opt/openreefgpu
CMD ["python3", "-m", "worker.handler"]
