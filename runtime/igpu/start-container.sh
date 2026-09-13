#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || ! "$1" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]]; then
  echo "usage: $0 CONTAINER_NAME" >&2
  exit 2
fi
sysfs_root="${SYSFS_ROOT:-/sys}"
device_root="${DEVICE_ROOT:-/dev/dri}"
device_alias="${device_root}/thoughtloud-igpu"
candidates=()
for candidate in "${sysfs_root}/class/drm"/renderD*; do
  if [[ -r "${candidate}/device/vendor" && -r "${candidate}/device/device" ]] && \
    [[ "$(< "${candidate}/device/vendor")" == 0x1002 && \
       "$(< "${candidate}/device/device")" == 0x1900 ]]; then
    candidates+=("${candidate##*/}")
  fi
done
if [[ ${#candidates[@]} -ne 1 ]]; then
  echo "expected exactly one Radeon 780M (1002:1900); found ${#candidates[@]}; refusing to start" >&2
  exit 1
fi
if [[ ! -L "${device_alias}" || ! -c "${device_alias}" ]] || \
  [[ "$(realpath "${device_alias}")" != "$(realpath "${device_root}/${candidates[0]}")" ]]; then
  echo "missing or mismatched hardware-identity GPU alias: ${device_alias}" >&2
  exit 1
fi
# Refuse to start an old PCI/render-number mapping or a container exposing other
# devices. Docker resolves this retained alias anew whenever it starts.
mapping=$(docker inspect --format '{{len .HostConfig.Devices}}|{{range .HostConfig.Devices}}{{.PathOnHost}}:{{.PathInContainer}}:{{.CgroupPermissions}};{{end}}' "$1")
if [[ "${mapping}" != "1|${device_alias}:/dev/dri/thoughtloud-igpu:rwm;" ]]; then
  echo "container $1 does not use the ThoughtLoud hardware-identity device mapping; explicit migration required" >&2
  exit 1
fi
exec docker start "$1"
