#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 CLEAN_ATOMIC_SOURCE_REPO NEW_BUILD_DIR" >&2
  exit 2
fi
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source_repo=$(realpath "$1")
build_dir=$(realpath -m "$2")
revision=0a635dcd92ba66c75fccfef91c3e106f4668f367
patch_file="${script_dir}/../igpu/atomic-mtp-audio.patch"
[[ ! -e "${build_dir}" ]] || { echo "Build directory already exists" >&2; exit 1; }
[[ "$(git -C "${source_repo}" rev-parse HEAD)" == "${revision}" ]] || {
  echo "Source must be at ${revision}" >&2; exit 1;
}
[[ -z "$(git -C "${source_repo}" status --porcelain --untracked-files=all)" ]] || {
  echo "Source checkout must be clean" >&2; exit 1;
}
temporary_root=$(mktemp -d)
cleanup() {
  git -C "${source_repo}" worktree remove --force "${temporary_root}/source" >/dev/null 2>&1 || true
  rm -rf -- "${temporary_root}"
}
trap cleanup EXIT
git -C "${source_repo}" worktree add --detach "${temporary_root}/source" "${revision}"
git -C "${temporary_root}/source" apply --check "${patch_file}"
git -C "${temporary_root}/source" apply "${patch_file}"
cmake -S "${temporary_root}/source" -B "${build_dir}" \
  -DBUILD_SHARED_LIBS=ON -DGGML_BACKEND_DL=ON -DGGML_CPU_ALL_VARIANTS=ON \
  -DGGML_NATIVE=OFF -DGGML_AVX=OFF -DGGML_AVX2=OFF -DGGML_FMA=OFF -DGGML_F16C=OFF \
  -DGGML_BMI2=OFF -DGGML_SSE42=OFF \
  -DGGML_VULKAN=ON -DGGML_CCACHE=OFF -DLLAMA_CURL=OFF \
  -DLLAMA_BUILD_SERVER=ON -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF \
  -DLLAMA_BUILD_TOOLS=ON -DCMAKE_BUILD_TYPE=Release
cmake --build "${build_dir}" --target llama-server -j"${BUILD_JOBS:-2}"
# CPU variants set their own feature flags. Keep both the portable fallback and
# optimized plugins beside llama-server so upstream can select a compatible one.
for backend in libggml-cpu-x64.so libggml-cpu-haswell.so libggml-vulkan.so; do
  [[ -s "${build_dir}/bin/${backend}" ]] || {
    echo "Native runtime is incomplete: missing ${backend}" >&2; exit 1;
  }
done
cp "${temporary_root}/source/LICENSE" "${build_dir}/bin/LICENSE.llama.cpp"
cp "${patch_file}" "${build_dir}/bin/voxd-audio.patch"
git -C "${source_repo}" archive --format=tar.gz --prefix=llama-source/ "${revision}" > "${build_dir}/bin/llama-source.tar.gz"
# Some vendors carry their notices inside their distributed headers.
mkdir -p "${build_dir}/bin/licenses"
while IFS= read -r -d '' notice; do
  relative=${notice#"${temporary_root}/source/"}
  destination="${build_dir}/bin/licenses/${relative}"
  mkdir -p "$(dirname "$destination")"
  cp "$notice" "$destination"
done < <(find "${temporary_root}/source/vendor" -type f \( -iname '*license*' -o -iname '*copying*' -o -iname '*notice*' -o -name 'json.hpp' -o -name 'miniaudio.h' -o -name 'stb_image.h' -o -name 'stb_image_resize.h' \) -print0)
cat > "${build_dir}/bin/BUILD.txt" <<EOF
Source: https://github.com/AtomicBot-ai/atomic-llama-cpp-turboquant
Revision: ${revision}
Patch: voxd-audio.patch
Build recipe: runtime/linux/build.sh
Target: Linux x86_64; optional Vulkan
CPU dispatch: portable x64 baseline plus optimized CPU plugins, selected by host features
Profile: Gemma E4B Q8 + F16 audio projector, MTP disabled
EOF
echo "Native runtime: ${build_dir}/bin"
