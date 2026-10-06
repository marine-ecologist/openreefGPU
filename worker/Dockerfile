# syntax=docker/dockerfile:1.7
# openreefGPU owns the cloud adapter. The reconstruction pipeline itself is
# installed from one immutable OpenReef desktop commit declared in versions.json.
FROM nvidia/cuda:12.9.1-devel-ubuntu24.04 AS openmvs-builder

ARG DEBIAN_FRONTEND=noninteractive
ARG OPENMVS_VERSION=2.4.0
ARG OPENMVS_REF=2d89278c287f11be126e7f46cc403ec4596b7721
ARG VCG_REF=658ba36d0a5666650da6e066b4794efc5a463407
ARG CGAL_VERSION=6.0.1
ARG CGAL_REF=50cfbde3b84dbeae8338268db2d78fe4fcb522de
COPY worker/patches/openmvs-disable-jpegxl.patch /tmp/openmvs-disable-jpegxl.patch
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake git ca-certificates python3 python3-dev \
    libboost-filesystem-dev libboost-graph-dev libboost-iostreams-dev \
    libboost-program-options-dev libboost-serialization-dev libboost-system-dev \
    libceres-dev libeigen3-dev libglew-dev libglfw3-dev libgmp-dev \
    libglu1-mesa-dev libjpeg-dev libmpfr-dev libnanoflann-dev libopencv-dev \
    libpng-dev libtiff-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
RUN git init /opt/cgal \
    && git -C /opt/cgal remote add origin https://github.com/CGAL/cgal.git \
    && git -C /opt/cgal fetch --depth 1 origin "${CGAL_REF}" \
    && git -C /opt/cgal checkout --detach FETCH_HEAD \
    && test "$(git -C /opt/cgal rev-parse HEAD)" = "${CGAL_REF}" \
    && cmake -S /opt/cgal -B /opt/cgal/build \
      -DCMAKE_BUILD_TYPE=Release -DWITH_examples=OFF -DWITH_demos=OFF \
    && cmake --build /opt/cgal/build --parallel "$(nproc)" \
    && cmake --install /opt/cgal/build --prefix /usr/local \
    && printf '%s\n' "${CGAL_VERSION}" > /usr/local/CGAL_VERSION
RUN git init /opt/vcglib \
    && git -C /opt/vcglib remote add origin https://github.com/cdcseacave/VCG.git \
    && git -C /opt/vcglib fetch --depth 1 origin "${VCG_REF}" \
    && git -C /opt/vcglib checkout --detach FETCH_HEAD \
    && test "$(git -C /opt/vcglib rev-parse HEAD)" = "${VCG_REF}" \
    && git init /opt/openMVS \
    && git -C /opt/openMVS remote add origin https://github.com/cdcseacave/openMVS.git \
    && git -C /opt/openMVS fetch --depth 1 origin "${OPENMVS_REF}" \
    && git -C /opt/openMVS checkout --detach FETCH_HEAD \
    && test "$(git -C /opt/openMVS rev-parse HEAD)" = "${OPENMVS_REF}" \
    && git -C /opt/openMVS apply --check /tmp/openmvs-disable-jpegxl.patch \
    && git -C /opt/openMVS apply /tmp/openmvs-disable-jpegxl.patch \
    && cmake -S /opt/openMVS -B /opt/openMVS/build \
      -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES="75;86;89;120" \
      -DCUDA_CUDA_LIBRARY=/usr/local/cuda/lib64/stubs/libcuda.so \
      -DOpenMVS_USE_CUDA=ON -DOpenMVS_BUILD_TOOLS=ON \
      -DOpenMVS_BUILD_VIEWER=OFF -DOpenMVS_USE_BREAKPAD=OFF \
      -DOpenMVS_USE_PYTHON=OFF -DOpenMVS_ENABLE_TESTS=OFF \
      -DVCG_ROOT=/opt/vcglib \
    && cmake --build /opt/openMVS/build --parallel "$(nproc)" \
    && cmake --install /opt/openMVS/build \
    && printf '%s\n' "${OPENMVS_VERSION}" > /usr/local/OPENMVS_VERSION

FROM colmap/colmap:20260907.8077
ARG DEBIAN_FRONTEND=noninteractive
ARG OPENREEF_REPOSITORY=https://github.com/marine-ecologist/openreef.git
ARG OPENREEF_VERSION=0.6.3
ARG OPENREEF_REF=c5da966804860b500a4f5657c783d9df9de9cbfc
ARG OPENREEF_GPU_VERSION=0.6.3-gpu.14

RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates python3 python3-pip \
    libboost-iostreams-dev libboost-program-options-dev libboost-serialization-dev \
    libceres-dev libglfw3 libopencv-dev \
    && rm -rf /var/lib/apt/lists/*
COPY --from=openmvs-builder /usr/local/ /usr/local/
COPY --from=openmvs-builder \
    /opt/openMVS/build/bin/InterfaceCOLMAP \
    /opt/openMVS/build/bin/DensifyPointCloud \
    /opt/openMVS/build/bin/ReconstructMesh \
    /opt/openMVS/build/bin/RefineMesh \
    /opt/openMVS/build/bin/TextureMesh \
    /opt/openMVS/build/bin/TransformScene \
    /usr/local/bin/

RUN git init /opt/openreef \
    && git -C /opt/openreef remote add origin "${OPENREEF_REPOSITORY}" \
    && git -C /opt/openreef fetch --depth 1 origin "${OPENREEF_REF}" \
    && git -C /opt/openreef checkout --detach FETCH_HEAD \
    && test "$(git -C /opt/openreef rev-parse HEAD)" = "${OPENREEF_REF}"
RUN python3 -m pip install --break-system-packages --no-cache-dir --no-deps /opt/openreef \
    && python3 -m pip install --break-system-packages --no-cache-dir \
      runpod==1.7.13 numpy==2.2.6 Pillow==11.3.0 opencv-python-headless==4.12.0.88 \
    && test "$(python3 -c 'import openreef; print(openreef.__version__)')" = "${OPENREEF_VERSION}"

COPY worker/ /opt/openreefgpu/worker/

ENV PYTHONUNBUFFERED=1 \
    STORAGE_PROVIDER=google-drive \
    OPENREEF_VERSION=${OPENREEF_VERSION} \
    OPENREEF_GPU_VERSION=${OPENREEF_GPU_VERSION} \
    OPENREEF_MAX_IMAGE_SIZE=3200 \
    OPENREEF_MAX_RESOLUTION=2560 \
    OPENREEF_MAX_TEXTURE_SIZE=4096 \
    OPENREEF_TEXTURE_RESOLUTION_LEVEL=0 \
    OPENREEF_TEXTURE_SHARPNESS=0 \
    OPENREEF_MAX_CORES=32 \
    GOOGLE_DRIVE_JOB_ROOT=jobs

WORKDIR /opt/openreefgpu
CMD ["python3", "-m", "worker.handler"]
