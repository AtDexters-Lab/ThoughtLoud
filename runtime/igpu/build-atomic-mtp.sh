#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 CLEAN_ATOMIC_SOURCE_REPO NEW_BUILD_DIR" >&2
  exit 2
fi

script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
source_repo=$(realpath "$1")
build_dir=$(realpath -m "$2")
mtp_patch="${script_dir}/atomic-mtp-audio.patch"
manifest_helper="${script_dir}/mtp_build_manifest.py"
required_commit="0a635dcd92ba66c75fccfef91c3e106f4668f367"

[[ -d "${source_repo}/.git" || -f "${source_repo}/.git" ]] || {
  echo "not a Git checkout: ${source_repo}" >&2
  exit 2
}
[[ -f "${mtp_patch}" ]] || {
  echo "missing VOXD Atomic MTP patch: ${mtp_patch}" >&2
  exit 2
}
[[ -f "${manifest_helper}" ]] || {
  echo "missing MTP build manifest helper: ${manifest_helper}" >&2
  exit 2
}
[[ ! -e "${build_dir}" && ! -L "${build_dir}" ]] || {
  echo "build directory already exists: ${build_dir}" >&2
  exit 1
}
[[ "${build_dir}" != "${source_repo}" && "${build_dir}" != "${source_repo}/"* ]] || {
  echo "build directory must be outside the Atomic source checkout: ${build_dir}" >&2
  exit 1
}

actual_commit=$(git -C "${source_repo}" rev-parse HEAD)
[[ "${actual_commit}" == "${required_commit}" ]] || {
  echo "Atomic source must be exactly ${required_commit}; found ${actual_commit}" >&2
  exit 1
}
[[ -z "$(git -C "${source_repo}" status --porcelain --untracked-files=all)" ]] || {
  echo "Atomic source checkout must be clean" >&2
  exit 1
}

temporary_root=$(mktemp -d "${TMPDIR:-/tmp}/voxd-atomic-mtp-build.XXXXXXXX")
source_tree="${temporary_root}/source"
applied_patch="${temporary_root}/applied.patch"
worktree_added=false

cleanup() {
  local status=$?
  trap - EXIT
  if [[ "${worktree_added}" == true ]]; then
    git -C "${source_repo}" worktree remove --force "${source_tree}" >/dev/null 2>&1 || true
  fi
  rm -rf -- "${temporary_root}"
  exit "${status}"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

git -C "${source_repo}" worktree add --detach "${source_tree}" "${required_commit}" >/dev/null
worktree_added=true
git -C "${source_tree}" apply --check "${mtp_patch}"
git -C "${source_tree}" apply "${mtp_patch}"
git -C "${source_tree}" diff --check
git -C "${source_tree}" diff --binary -- \
  common/speculative.cpp common/speculative.h tools/server/server-context.cpp \
  >"${applied_patch}"
if ! cmp -s "${mtp_patch}" "${applied_patch}"; then
  echo "applied Atomic source diff does not exactly match the VOXD patch" >&2
  exit 1
fi

cmake_options=(
  -DGGML_VULKAN=ON
  -DGGML_CCACHE=OFF
  -DLLAMA_CURL=OFF
  -DLLAMA_BUILD_SERVER=ON
  -DLLAMA_BUILD_TESTS=OFF
  -DLLAMA_BUILD_EXAMPLES=OFF
  -DLLAMA_BUILD_TOOLS=ON
  -DCMAKE_BUILD_TYPE=Release
)
cmake -S "${source_tree}" -B "${build_dir}" "${cmake_options[@]}"
cmake --build "${build_dir}" --target llama-server -j"${BUILD_JOBS:-4}"

server="${build_dir}/bin/llama-server"
[[ -x "${server}" ]] || {
  echo "Atomic build did not produce ${server}" >&2
  exit 1
}
for marker in --mtp-head --spec-type --draft-block-size; do
  grep -aFq -- "${marker}" "${server}" || {
    echo "built llama-server is missing required flag ${marker}" >&2
    exit 1
  }
done

python3 "${manifest_helper}" create \
  --runtime-dir "${build_dir}/bin" \
  --patch "${mtp_patch}" \
  --manifest "${build_dir}/bin/voxd-mtp-build-manifest.json"

echo "Atomic Vulkan MTP build ready at ${build_dir}/bin"
