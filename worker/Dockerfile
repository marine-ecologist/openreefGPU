# syntax=docker/dockerfile:1.7
# openreefGPU owns the cloud adapter. The reconstruction pipeline itself is
# installed from one immutable OpenReef desktop commit declared in versions.json.
FROM nvidia/cuda:12.9.1-devel-ubuntu24.04 AS colmap-caspar-builder

ARG DEBIAN_FRONTEND=noninteractive
ARG COLMAP_VERSION=main-2026-10-07
ARG COLMAP_REF=68b722be23fdba964a24583720bde07675171d47
ARG COLMAP_CUDA_ARCHITECTURES=75;80;86;89;90;100;103;120
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential ca-certificates cmake git ninja-build libblas-dev liblapack-dev \
    libboost-program-options-dev libboost-graph-dev libboost-system-dev \
    libcgal-dev libceres-dev libcurl4-openssl-dev libeigen3-dev libglew-dev \
    libgoogle-glog-dev libgmock-dev libgtest-dev libmetis-dev libmkl-full-dev \
    libopenimageio-dev openimageio-tools libssl-dev libsqlite3-dev \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /usr/include/opencv4
RUN git init /opt/colmap \
    && git -C /opt/colmap remote add origin https://github.com/colmap/colmap.git \
    && git -C /opt/colmap fetch --depth 1 origin "${COLMAP_REF}" \
    && git -C /opt/colmap checkout --detach FETCH_HEAD \
    && test "$(git -C /opt/colmap rev-parse HEAD)" = "${COLMAP_REF}" \
    && cmake -S /opt/colmap -B /opt/colmap/build -GNinja \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_CUDA_ARCHITECTURES="${COLMAP_CUDA_ARCHITECTURES}" \
      -DCMAKE_INSTALL_PREFIX=/opt/colmap-install \
      -DCASPAR_ENABLED=ON -DCASPAR_USE_DOUBLE=OFF \
      -DGUI_ENABLED=OFF -DTESTS_ENABLED=OFF -DONNX_ENABLED=OFF \
    && grep -q '^CASPAR_ENABLED:BOOL=ON$' /opt/colmap/build/CMakeCache.txt \
    && cmake --build /opt/colmap/build --parallel 2 \
    && cmake --install /opt/colmap/build \
    && mkdir -p /opt/colmap-install/share/openreef \
    && printf '%s\n' "${COLMAP_VERSION}" > /opt/colmap-install/share/openreef/COLMAP_VERSION \
    && printf '%s\n' "${COLMAP_REF}" > /opt/colmap-install/share/openreef/COLMAP_REF \
    && printf '%s\n' 'CASPAR_ENABLED=ON' > /opt/colmap-install/share/openreef/COLMAP_BUILD_OPTIONS

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
ARG OPENREEF_VERSION=0.6.4
ARG OPENREEF_REF=e396b72933c4f8ad8f5d2e5175b124c51daed52d
ARG OPENREEF_GPU_VERSION=0.6.4-gpu.2
ARG COLMAP_REF=68b722be23fdba964a24583720bde07675171d47

RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates python3 python3-pip \
    libboost-iostreams-dev libboost-program-options-dev libboost-serialization-dev \
    libceres-dev libglfw3 libopencv-dev \
    && rm -rf /var/lib/apt/lists/*
COPY --from=openmvs-builder /usr/local/ /usr/local/
COPY --from=colmap-caspar-builder /opt/colmap-install/ /usr/local/
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
    && test "$(python3 -c 'import openreef; print(openreef.__version__)')" = "${OPENREEF_VERSION}" \
    && test "$(cat /usr/local/share/openreef/COLMAP_REF)" = "${COLMAP_REF}" \
    && test "$(cat /usr/local/share/openreef/COLMAP_BUILD_OPTIONS)" = 'CASPAR_ENABLED=ON' \
    && colmap mapper --help 2>&1 | grep -q 'Mapper.ba_local_backend'

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
    OPENREEF_BA_BACKEND=caspar \
    OPENREEF_CERES_USE_GPU=0 \
    OPENREEF_BA_GPU_INDEX=-1 \
    GOOGLE_DRIVE_JOB_ROOT=jobs

WORKDIR /opt/openreefgpu
CMD ["python3", "-m", "worker.handler"]
