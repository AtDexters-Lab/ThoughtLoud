#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: $0 LLAMA_BUILD_BIN_DIR LLAMA_SWAP_BINARY MTP_HEAD_GGUF" >&2
  exit 2
fi

script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(cd -- "${script_dir}/../.." && pwd)
build_bin_dir=$(realpath "$1")
llama_swap_binary=$(realpath "$2")
mtp_head_source=$(realpath "$3")
voxd_python="${VOXD_PYTHON:-${repo_dir}/.venv/bin/python}"
runtime_parent="${XDG_DATA_HOME:-$HOME/.local/share}/voxd"
runtime_dir="${runtime_parent}/llama-vulkan"
config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/voxd"
runtime_config_dir="${config_dir}/igpu-runtime"
runtime_config="${runtime_config_dir}/llama-swap.yaml"
runtime_device="${runtime_config_dir}/igpu-render"
voxd_config="${config_dir}/config.yaml"
container_name="${CONTAINER_NAME:-voxd-gemma-igpu}"
host_port="${HOST_PORT:-9394}"
render_node="${RENDER_NODE:-}"
device_root="${DEVICE_ROOT:-/dev/dri}"
sysfs_root="${SYSFS_ROOT:-/sys}"
hf_volume="${HF_VOLUME:-lemonade-docker_hf-cache}"
runtime_image="${RUNTIME_IMAGE:-gemma-mtp-serve}"
runtime_model="gemma-e4b"
build_manifest_name="voxd-mtp-build-manifest.json"
build_manifest="${build_bin_dir}/${build_manifest_name}"

[[ -x "${build_bin_dir}/llama-server" ]] || {
  echo "missing llama-server in ${build_bin_dir}" >&2
  exit 2
}
[[ -x "${llama_swap_binary}" ]] || {
  echo "missing llama-swap binary: ${llama_swap_binary}" >&2
  exit 2
}
[[ -f "${mtp_head_source}" && -s "${mtp_head_source}" ]] || {
  echo "missing or empty E4B MTP assistant GGUF: ${mtp_head_source}" >&2
  exit 2
}
[[ -x "${voxd_python}" ]] || {
  echo "missing VOXD Python environment: ${voxd_python}; run ./setup.sh first" >&2
  exit 2
}
[[ -f "${build_manifest}" ]] || {
  echo "missing VOXD MTP build manifest: ${build_manifest}" >&2
  exit 2
}
"${voxd_python}" "${script_dir}/mtp_build_manifest.py" verify \
  --runtime-dir "${build_bin_dir}" \
  --patch "${script_dir}/atomic-mtp-audio.patch" \
  --manifest "${build_manifest}" \
  --mtp-head "${mtp_head_source}"
# DRM render numbers can change across boots. Identify the validated Radeon
# 780M through sysfs, then keep its PCI by-path name in Docker's device mapping.
is_validated_igpu() {
  local device="${sysfs_root}/class/drm/$1/device"
  [[ -r "${device}/vendor" && -r "${device}/device" ]] && \
    [[ "$(< "${device}/vendor")" == 0x1002 && \
       "$(< "${device}/device")" == 0x1900 ]]
}
if [[ -z "${render_node}" ]]; then
  candidates=()
  for candidate in "${sysfs_root}/class/drm"/renderD*; do
    node=${candidate##*/}
    if is_validated_igpu "${node}"; then
      candidates+=("${node}")
    fi
  done
  if [[ ${#candidates[@]} -ne 1 ]]; then
    echo "expected one Radeon 780M (1002:1900) render device; found ${#candidates[@]}. Set RENDER_NODE only to select among matching devices." >&2
    exit 2
  fi
  render_node=${candidates[0]}
fi
if [[ ! "${render_node}" =~ ^renderD[0-9]+$ ]] || ! is_validated_igpu "${render_node}"; then
  echo "selected render node is not the validated Radeon 780M (1002:1900): ${render_node}" >&2
  exit 2
fi
[[ -e "${device_root}/${render_node}" ]] || {
  echo "missing iGPU render node: ${device_root}/${render_node}" >&2
  exit 2
}
pci_device=$(realpath "${sysfs_root}/class/drm/${render_node}/device")
pci_address=${pci_device##*/}
if [[ ! "${pci_address}" =~ ^[[:xdigit:]]{4}:[[:xdigit:]]{2}:[[:xdigit:]]{2}\.[0-7]$ ]]; then
  echo "cannot identify the iGPU PCI address from ${pci_device}" >&2
  exit 2
fi
stable_render_path="${device_root}/by-path/pci-${pci_address}-render"
if [[ ! -L "${stable_render_path}" || ! -e "${stable_render_path}" ]] || \
  [[ "$(realpath "${stable_render_path}")" != "$(realpath "${device_root}/${render_node}")" ]]; then
  echo "missing or mismatched stable iGPU device path: ${stable_render_path}; restore the PCI by-path symlink before installing" >&2
  exit 2
fi
docker volume inspect "${hf_volume}" >/dev/null
docker image inspect "${runtime_image}" >/dev/null

if docker container inspect "${container_name}" >/dev/null 2>&1; then
  echo "VOXD iGPU runtime already exists as ${container_name}; refusing to modify it" >&2
  exit 1
fi
for managed_path in "${runtime_dir}" "${runtime_config_dir}"; do
  if [[ -e "${managed_path}" || -L "${managed_path}" ]]; then
    echo "VOXD iGPU installation path already exists: ${managed_path}" >&2
    exit 1
  fi
done

# Refuse malformed user configuration before creating any install artifacts.
"${voxd_python}" "${script_dir}/configure_voxd.py" --check "${voxd_config}"

runtime_dir_created=false
runtime_config_dir_created=false
container_id=""
voxd_config_existed=false
voxd_config_backup=""
config_changed=false
tray_was_active=false
committed=false

rollback() {
  local status=$?
  local retain_runtime=false
  local retain_config_backup=false
  trap - EXIT

  if [[ "${status}" -ne 0 && "${committed}" != true ]]; then
    if [[ -n "${container_id}" ]] && \
      ! docker rm -f "${container_id}" >/dev/null 2>&1; then
      retain_runtime=true
      echo "warning: retaining runtime files because container ${container_id} remains" >&2
    fi

    if [[ "${config_changed}" == true ]]; then
      if [[ "${voxd_config_existed}" == true ]]; then
        if ! cp -a "${voxd_config_backup}" "${voxd_config}"; then
          retain_config_backup=true
          echo "warning: could not restore VOXD config; recovery backup retained at ${voxd_config_backup}" >&2
        fi
      elif ! rm -f -- "${voxd_config}"; then
        echo "warning: could not remove candidate VOXD config ${voxd_config}" >&2
      fi
      if [[ "${tray_was_active}" == true ]]; then
        systemctl --user restart voxd-tray.service >/dev/null 2>&1 || true
      fi
    fi

    if [[ "${retain_runtime}" == false ]]; then
      if [[ "${runtime_config_dir_created}" == true ]]; then
        rm -rf -- "${runtime_config_dir}" || \
          echo "warning: could not remove ${runtime_config_dir}" >&2
      fi
      if [[ "${runtime_dir_created}" == true ]]; then
        rm -rf -- "${runtime_dir}" || \
          echo "warning: could not remove ${runtime_dir}" >&2
      fi
    fi
    echo "VOXD iGPU installation failed; newly created resources were rolled back" >&2
  fi

  if [[ -n "${voxd_config_backup}" && "${retain_config_backup}" == false ]]; then
    rm -f -- "${voxd_config_backup}" || \
      echo "warning: could not remove ${voxd_config_backup}" >&2
  fi
  exit "${status}"
}
trap rollback EXIT

# These shared parents are harmless when empty and are never rollback-owned.
# Only the two exact managed directories below are exclusively claimed.
install -d -m 0700 "${runtime_parent}" "${config_dir}"

# These exact targets are the install lock. Concurrent or stale installs fail
# without gaining ownership and therefore cannot remove another invocation's files.
mkdir -m 0700 "${runtime_dir}"
runtime_dir_created=true
mkdir -m 0700 "${runtime_config_dir}"
runtime_config_dir_created=true

"${voxd_python}" "${script_dir}/mtp_build_manifest.py" stage \
  --runtime-dir "${build_bin_dir}" \
  --destination-dir "${runtime_dir}" \
  --patch "${script_dir}/atomic-mtp-audio.patch" \
  --manifest "${build_manifest}" \
  --mtp-head "${mtp_head_source}"
install -m 0755 "${llama_swap_binary}" "${runtime_dir}/llama-swap"
install -m 0600 "${script_dir}/llama-swap.yaml" "${runtime_config}"
# Docker's CLI uses colons as --device separators, including the colons in PCI
# by-path filenames. Keep a colon-free alias in our exclusively owned directory;
# Docker stores the alias and resolves the PCI symlink again at each start.
ln -s -- "${stable_render_path}" "${runtime_device}"

# docker create returns the exact resource owned by this invocation. Rollback
# removes by ID, never by a shared or guessed name.
container_id=$(docker create \
  --name "${container_name}" \
  --restart unless-stopped \
  --device "${runtime_device}:/dev/dri/renderD128" \
  -v "${hf_volume}:/hf:ro" \
  -v "${runtime_dir}:/opt/voxd/llama:ro" \
  -v "${runtime_config}:/config.yaml:ro" \
  -v /usr/lib/x86_64-linux-gnu:/usr/lib/x86_64-linux-gnu:ro \
  -v /usr/share/vulkan/icd.d:/usr/share/vulkan/icd.d:ro \
  -e LD_LIBRARY_PATH=/opt/voxd/llama \
  -e VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/radeon_icd.json \
  -e LLAMA_MTP_SKIP_STREAK_THRESHOLD=0 \
  -p "127.0.0.1:${host_port}:8080" \
  --entrypoint /opt/voxd/llama/llama-swap \
  "${runtime_image}" \
  -config /config.yaml -listen 0.0.0.0:8080)
docker start "${container_id}" >/dev/null

front_door_ready=false
for _ in {1..20}; do
  if curl --silent --show-error --fail --max-time 2 \
    "http://127.0.0.1:${host_port}/v1/models" >/dev/null; then
    front_door_ready=true
    break
  fi
  sleep 0.5
done

audio_smoke_payload=$(
  "${voxd_python}" -c '
import base64
import io
import json
import sys
import wave

output = io.BytesIO()
with wave.open(output, "wb") as audio:
    audio.setnchannels(1)
    audio.setsampwidth(2)
    audio.setframerate(16000)
    audio.writeframes(bytes(8000))

print(json.dumps({
    "model": sys.argv[1],
    "messages": [{
        "role": "user",
        "content": [
            {"type": "text", "text": "Transcribe this audio. If there is no intelligible speech, output exactly: no speech detected. Output only the transcript."},
            {"type": "input_audio", "input_audio": {
                "data": base64.b64encode(output.getvalue()).decode("ascii"),
                "format": "wav",
            }},
        ],
    }],
    "stream": False,
    "temperature": 0,
    "max_tokens": 16,
    "chat_template_kwargs": {"enable_thinking": False},
}))
' "${runtime_model}"
)
audio_smoke_response=""
if [[ "${front_door_ready}" != true ]] || ! audio_smoke_response=$(curl \
  --silent --show-error --fail --max-time 120 \
  "http://127.0.0.1:${host_port}/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d "${audio_smoke_payload}"); then
  docker logs "${container_id}" >&2 || true
  exit 1
fi
if ! "${voxd_python}" -c \
  'import json, sys; body = json.load(sys.stdin); content = body["choices"][0]["message"]["content"]; draft_n = body.get("timings", {}).get("draft_n"); sys.exit(0 if isinstance(content, str) and content.strip() and isinstance(draft_n, int) and not isinstance(draft_n, bool) and draft_n > 0 else 1)' \
  <<<"${audio_smoke_response}"; then
  echo "audio smoke did not prove active MTP drafting" >&2
  docker logs "${container_id}" >&2 || true
  exit 1
fi

mtp_text_payload=$(
  "${voxd_python}" -c '
import json

print(json.dumps({
    "model": "gemma-e4b",
    "messages": [{
        "role": "user",
        "content": "Reply with exactly: mixed mode is stable",
    }],
    "stream": False,
    "temperature": 0,
    "max_tokens": 32,
    "chat_template_kwargs": {"enable_thinking": False},
}))
'
)
for _ in 1 2; do
  mtp_text_response=""
  if ! mtp_text_response=$(curl \
    --silent --show-error --fail --max-time 120 \
    "http://127.0.0.1:${host_port}/v1/chat/completions" \
    -H 'Content-Type: application/json' \
    -d "${mtp_text_payload}"); then
    docker logs "${container_id}" >&2 || true
    exit 1
  fi
  if ! "${voxd_python}" -c \
    'import json, sys; body = json.load(sys.stdin); content = body["choices"][0]["message"]["content"]; draft_n = body.get("timings", {}).get("draft_n"); sys.exit(0 if content == "mixed mode is stable" and isinstance(draft_n, int) and not isinstance(draft_n, bool) and draft_n > 0 else 1)' \
    <<<"${mtp_text_response}"; then
    echo "same-server text smoke did not prove stable MTP drafting" >&2
    docker logs "${container_id}" >&2 || true
    exit 1
  fi
done

if [[ -f "${voxd_config}" ]]; then
  voxd_config_backup=$(mktemp "${config_dir}/.config.yaml.backup.XXXXXXXX")
  cp -a "${voxd_config}" "${voxd_config_backup}"
  voxd_config_existed=true
fi
if command -v systemctl >/dev/null 2>&1 && \
  systemctl --user is-active --quiet voxd-tray.service; then
  tray_was_active=true
fi
config_changed=true
"${voxd_python}" "${script_dir}/configure_voxd.py" \
  "${voxd_config}" "http://127.0.0.1:${host_port}" "${runtime_model}"

tray_restarted=false
if [[ "${tray_was_active}" == true ]]; then
  systemctl --user restart voxd-tray.service
  tray_restarted=true
fi

committed=true
if [[ -n "${voxd_config_backup}" ]]; then
  rm -f -- "${voxd_config_backup}" || \
    echo "warning: could not remove ${voxd_config_backup}" >&2
  voxd_config_backup=""
fi

echo "Q8 iGPU endpoint ready at http://127.0.0.1:${host_port}"
echo "VOXD configured to use gemma-e4b at that endpoint"
if [[ "${tray_restarted}" == true ]]; then
  echo "VOXD tray restarted"
else
  echo "VOXD tray was not active; it will use the iGPU endpoint on next start"
fi
