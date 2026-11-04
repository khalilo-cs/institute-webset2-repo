#!/usr/bin/env bash
# Builds llama.cpp's llama-server as fully static arm64 executables for the APK:
#   core/bin/libllamaserver.so          generic ARMv8-A (any 64-bit phone)
#   core/bin/libllamaserver_dotprod.so  ARMv8.2-A + dot-product (zig -mcpu=cortex_a55) (most phones since ~2018, much faster)
# Static musl binaries do not depend on Android's libraries, so no NDK is needed.
# Requirements: python3, cmake, network access to PyPI.
# Optional check before packaging: qemu-aarch64-static core/bin/libllamaserver.so --version
set -euo pipefail
cd "$(dirname "$0")"
LCP_VERSION=${LCP_VERSION:-0.3.36}           # llama-cpp-python sdist, which vendors llama.cpp
WORK=${WORK:-$PWD/../build/llama}
mkdir -p "$WORK" bin
python3 -m venv "$WORK/venv"
"$WORK/venv/bin/pip" install -q "ziglang==0.14.0"
ZIG="$WORK/venv/bin/python -m ziglang"
if [ ! -d "$WORK/src" ]; then
  url=$(python3 -c "import json,urllib.request;d=json.load(urllib.request.urlopen('https://pypi.org/pypi/llama-cpp-python/$LCP_VERSION/json'));print([u['url'] for u in d['urls'] if u['packagetype']=='sdist'][0])")
  curl -sL "$url" | tar xz -C "$WORK" && mv "$WORK/llama_cpp_python-$LCP_VERSION/vendor/llama.cpp" "$WORK/src"
fi
build() {  # $1 = output name, $2 = zig -mcpu value (a real core name)
  CC="$ZIG cc -target aarch64-linux-musl -mcpu=$2" CXX="$ZIG c++ -target aarch64-linux-musl -mcpu=$2" \
  cmake -S "$WORK/src" -B "$WORK/build-$1" -DCMAKE_SYSTEM_NAME=Linux -DCMAKE_SYSTEM_PROCESSOR=aarch64 \
    -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DLLAMA_CURL=OFF -DLLAMA_OPENSSL=OFF \
    -DGGML_OPENMP=OFF -DGGML_NATIVE=OFF \
    -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_SERVER=ON -DLLAMA_BUILD_TOOLS=ON \
    -DCMAKE_EXE_LINKER_FLAGS=-static >/dev/null
  cmake --build "$WORK/build-$1" --target llama-server -j"$(nproc)"
  cp "$WORK/build-$1/bin/llama-server" "bin/$1.so"
}
build libllamaserver generic              # ARMv8-A baseline
build libllamaserver_dotprod cortex_a55   # ARMv8.2-A + dotprod + fp16
ls -la bin/
