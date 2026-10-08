# syntax=docker/dockerfile:1.7
# openreefGPU owns the cloud adapter. The reconstruction pipeline itself is
# installed from one immutable OpenReef desktop commit declared in versions.json.
FROM nvidia/cuda:12.9.1-devel-ubuntu24.04 AS colmap-ceres-cuda-builder

ARG DEBIAN_FRONTEND=noninteractive
ARG CERES_VERSION=2.3.0-dev-2026-10-09
ARG CERES_REF=71be12da7161b567990f165e38771dc9c7e4e96b
ARG COLMAP_VERSION=main-2026-10-07
ARG COLMAP_REF=68b722be23fdba964a24583720bde07675171d47
ARG COLMAP_CUDA_ARCHITECTURES=75;80;86;89;90;100;103;120
COPY worker/patches/ceres-preserve-cuda-architectures.patch /tmp/ceres-preserve-cuda-architectures.patch
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential ca-certificates cmake git ninja-build libblas-dev liblapack-dev \
    libboost-program-options-dev libboost-graph-dev libboost-system-dev \
    libcgal-dev libcudss0-dev-cuda-12 libcudss0-static-cuda-12 \
    libcurl4-openssl-dev libeigen3-dev libglew-dev \
    libgoogle-glog-dev libgmock-dev libgtest-dev libmetis-dev libmkl-full-dev \
    libopenimageio-dev openimageio-tools libsuitesparse-dev libssl-dev libsqlite3-dev \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /usr/include/opencv4
RUN git init /opt/ceres \
    && git -C /opt/ceres remote add origin https://github.com/ceres-solver/ceres-solver.git \
    && git -C /opt/ceres fetch --depth 1 origin "${CERES_REF}" \
    && git -C /opt/ceres checkout --detach FETCH_HEAD \
    && test "$(git -C /opt/ceres rev-parse HEAD)" = "${CERES_REF}" \
    && git -C /opt/ceres submodule update --init --depth 1 \
    && git -C /opt/ceres apply --check /tmp/ceres-preserve-cuda-architectures.patch \
    && git -C /opt/ceres apply /tmp/ceres-preserve-cuda-architectures.patch \
    && cmake -S /opt/ceres -B /opt/ceres/build -GNinja \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_CUDA_ARCHITECTURES="${COLMAP_CUDA_ARCHITECTURES}" \
      -DCMAKE_INSTALL_PREFIX=/opt/ceres-install \
      -DCMAKE_INSTALL_LIBDIR=lib \
      -Dcudss_DIR=/usr/lib/x86_64-linux-gnu/libcudss/12/cmake/cudss \
      -Dcudss_INCLUDE_DIR=/usr/include/libcudss/12 \
      -Dcudss_LIBRARY_DIR=/usr/lib/x86_64-linux-gnu/libcudss/12 \
      -DBUILD_BENCHMARKS=OFF -DBUILD_DOCUMENTATION=OFF \
      -DBUILD_EXAMPLES=OFF -DBUILD_TESTING=OFF -DBUILD_SHARED_LIBS=ON \
      -DWITH_CUDA=default -DWITH_SUITESPARSE=ON \
    && grep -q 'CERES_COMPILED_COMPONENTS.*CUDA' /opt/ceres/build/CeresConfig.cmake \
    && grep -q 'CERES_COMPILED_COMPONENTS.*cuDSS' /opt/ceres/build/CeresConfig.cmake \
    && cmake --build /opt/ceres/build --parallel 2 \
    && cmake --install /opt/ceres/build \
    && mkdir -p /opt/ceres-install/share/openreef \
    && printf '%s\n' "${CERES_VERSION}" > /opt/ceres-install/share/openreef/CERES_VERSION \
    && printf '%s\n' "${CERES_REF}" > /opt/ceres-install/share/openreef/CERES_REF \
    && printf '%s\n' 'CUDA_ENABLED=ON' 'CUDSS_ENABLED=ON' > /opt/ceres-install/share/openreef/CERES_BUILD_OPTIONS
RUN git init /opt/colmap \
    && git -C /opt/colmap remote add origin https://github.com/colmap/colmap.git \
    && git -C /opt/colmap fetch --depth 1 origin "${COLMAP_REF}" \
    && git -C /opt/colmap checkout --detach FETCH_HEAD \
    && test "$(git -C /opt/colmap rev-parse HEAD)" = "${COLMAP_REF}" \
    && cmake -S /opt/colmap -B /opt/colmap/build -GNinja \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_CUDA_ARCHITECTURES="${COLMAP_CUDA_ARCHITECTURES}" \
      -DCMAKE_INSTALL_PREFIX=/opt/colmap-install \
      -DCMAKE_PREFIX_PATH=/opt/ceres-install \
      -DCeres_DIR=/opt/ceres-install/lib/cmake/Ceres \
      -Dcudss_DIR=/usr/lib/x86_64-linux-gnu/libcudss/12/cmake/cudss \
      -Dcudss_INCLUDE_DIR=/usr/include/libcudss/12 \
      -Dcudss_LIBRARY_DIR=/usr/lib/x86_64-linux-gnu/libcudss/12 \
      -DCASPAR_ENABLED=ON -DCASPAR_USE_DOUBLE=OFF \
      -DGUI_ENABLED=OFF -DTESTS_ENABLED=OFF -DONNX_ENABLED=OFF \
    && grep -q '^CASPAR_ENABLED:BOOL=ON$' /opt/colmap/build/CMakeCache.txt \
    && grep -q '^Ceres_DIR:PATH=/opt/ceres-install/lib/cmake/Ceres$' /opt/colmap/build/CMakeCache.txt \
    && cmake --build /opt/colmap/build --parallel 2 \
    && cmake --install /opt/colmap/build \
    && mkdir -p /opt/colmap-install/share/openreef \
    && printf '%s\n' "${COLMAP_VERSION}" > /opt/colmap-install/share/openreef/COLMAP_VERSION \
    && printf '%s\n' "${COLMAP_REF}" > /opt/colmap-install/share/openreef/COLMAP_REF \
    && printf '%s\n' 'CASPAR_ENABLED=ON' 'CERES_CUDA_ENABLED=ON' 'CERES_CUDSS_ENABLED=ON' > /opt/colmap-install/share/openreef/COLMAP_BUILD_OPTIONS

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
ARG OPENREEF_VERSION=0.6.5
ARG OPENREEF_REF=7329fe43e3d43f605e9961c608af81e72788b475
ARG OPENREEF_GPU_VERSION=0.6.5-gpu.1
ARG CERES_REF=71be12da7161b567990f165e38771dc9c7e4e96b
ARG COLMAP_REF=68b722be23fdba964a24583720bde07675171d47

RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates python3 python3-pip \
    libblas-dev liblapack-dev \
    libboost-graph-dev libboost-iostreams-dev libboost-program-options-dev \
    libboost-serialization-dev libboost-system-dev libcgal-dev libcudss0-cuda-12 \
    libcurl4-openssl-dev libeigen3-dev libglew-dev libglfw3 \
    libgoogle-glog-dev libmetis-dev libmkl-full-dev libopencv-dev \
    libopenimageio-dev openimageio-tools libssl-dev libsqlite3-dev \
    && rm -rf /var/lib/apt/lists/*
COPY --from=openmvs-builder /usr/local/ /usr/local/
COPY --from=colmap-ceres-cuda-builder /opt/ceres-install/ /usr/local/
COPY --from=colmap-ceres-cuda-builder /opt/colmap-install/ /usr/local/
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
RUN python3 -m pip install --break-system-packages --no-cache-dir --no-deps /opt/openreef
RUN python3 -m pip install --break-system-packages --no-cache-dir \
      runpod==1.7.13 numpy==2.2.6 Pillow==11.3.0 opencv-python-headless==4.12.0.88
RUN test "$(python3 -c 'import openreef; print(openreef.__version__)')" = "${OPENREEF_VERSION}"
RUN ldconfig \
    && test "$(cat /usr/local/share/openreef/CERES_REF)" = "${CERES_REF}" \
    && grep -qx 'CUDA_ENABLED=ON' /usr/local/share/openreef/CERES_BUILD_OPTIONS \
    && grep -qx 'CUDSS_ENABLED=ON' /usr/local/share/openreef/CERES_BUILD_OPTIONS \
    && test "$(cat /usr/local/share/openreef/COLMAP_REF)" = "${COLMAP_REF}" \
    && grep -qx 'CERES_CUDA_ENABLED=ON' /usr/local/share/openreef/COLMAP_BUILD_OPTIONS \
    && grep -qx 'CERES_CUDSS_ENABLED=ON' /usr/local/share/openreef/COLMAP_BUILD_OPTIONS \
    && ldd "$(command -v colmap)" | grep -q '/usr/local/lib/libceres'
RUN test -x "$(command -v colmap)" \
    && ! ldd "$(command -v colmap)" | grep -q 'not found'
RUN colmap --help >/dev/null

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
    OPENREEF_BA_BACKEND=ceres \
    OPENREEF_CERES_USE_GPU=1 \
    OPENREEF_BA_GPU_INDEX=-1 \
    GOOGLE_DRIVE_JOB_ROOT=jobs

WORKDIR /opt/openreefgpu
CMD ["python3", "-m", "worker.handler"]
