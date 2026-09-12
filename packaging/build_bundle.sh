#!/usr/bin/env bash
set -euo pipefail

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
build_python="${VOXD_BUILD_PYTHON:-${repo_dir}/.venv-build/bin/python}"
if [[ ! -x "${build_python}" ]]; then
  echo "Create a clean build environment first; see packaging/README.md" >&2
  exit 1
fi
cd "${repo_dir}"
: "${VOXD_LLAMA_RUNTIME:?Set VOXD_LLAMA_RUNTIME to the native runtime bin directory}"
: "${VOXD_YDOTOOL_RUNTIME:?Set VOXD_YDOTOOL_RUNTIME to the build_ydotool.sh output}"
test -x "${VOXD_LLAMA_RUNTIME}/llama-server"
test -x "${VOXD_YDOTOOL_RUNTIME}/libexec/ydotoold"
test -f "${VOXD_YDOTOOL_RUNTIME}/licenses/ydotool-source.tar.gz"
"${build_python}" -m pip install --no-index --no-deps --no-build-isolation "${repo_dir}"
"${build_python}" -m PyInstaller --noconfirm --clean packaging/thoughtloud.spec
cp -a "${VOXD_YDOTOOL_RUNTIME}/libexec" "${repo_dir}/dist/thoughtloud/"
cp -a "${VOXD_YDOTOOL_RUNTIME}/licenses" "${repo_dir}/dist/thoughtloud/"

ln -s thoughtloud "${repo_dir}/dist/thoughtloud/voxd"

# A bundle must work without the source tree or the build environment on sys.path.
temporary_config=$(mktemp -d)
trap 'rm -rf -- "${temporary_config}"' EXIT
env -u PYTHONPATH -u PYTHONHOME \
  XDG_CONFIG_HOME="${temporary_config}/config" \
  XDG_DATA_HOME="${temporary_config}/data" \
  QT_QPA_PLATFORM=offscreen \
  "${repo_dir}/dist/thoughtloud/thoughtloud" --version
echo "Application bundle: ${repo_dir}/dist/thoughtloud"
