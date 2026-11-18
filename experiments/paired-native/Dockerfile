# Temporary comparison only; production images and dependency versions are unchanged.
FROM ghcr.io/marine-ecologist/openreef-gpu:0.6.5-gpu.10-joint.4
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake libcudss0-dev-cuda-12 libabsl-dev \
    && rm -rf /var/lib/apt/lists/*
COPY experiments/paired-native/ /opt/paired-native/
RUN cmake -S /opt/paired-native -B /opt/paired-native/build \
    -DCMAKE_BUILD_TYPE=Release -DCeres_DIR=/usr/local/lib/cmake/Ceres \
    -Dcudss_DIR=/usr/lib/x86_64-linux-gnu/libcudss/12/cmake/cudss \
    -Dcudss_INCLUDE_DIR=/usr/include/libcudss/12 \
    -Dcudss_LIBRARY_DIR=/usr/lib/x86_64-linux-gnu/libcudss/12 \
    && cmake --build /opt/paired-native/build --parallel 2
RUN python3 -m pip install --break-system-packages --no-cache-dir 'pytest>=8,<10' \
    && PAIRED_BA_BINARY=/opt/paired-native/build/paired-baseline-ba \
       python3 -m pytest /opt/paired-native/test_geometry.py -q
WORKDIR /opt/paired-native
CMD ["python3", "-u", "/opt/paired-native/serverless.py"]
