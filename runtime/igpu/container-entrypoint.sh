#!/bin/sh
set -eu

if [ "$#" -eq 0 ]; then
  echo "usage: $0 COMMAND [ARG ...]" >&2
  exit 2
fi

# Mesa looks for the render node matching the device's actual minor number.
# Docker receives a stable hardware-ID alias, so restore that canonical name
# inside the container without assuming the host's current render index.
device_root=${DEVICE_ROOT:-/dev/dri}
runtime_device=${device_root}/thoughtloud-igpu

render_minor() (
  device_info=$(LC_ALL=C stat -Lc '%F:%t:%T' -- "$1") || exit 1
  case "${device_info}" in
    'character special file:'*) device_numbers=${device_info#character special file:} ;;
    *) exit 1 ;;
  esac
  major_hex=${device_numbers%%:*}
  minor_hex=${device_numbers#*:}
  case "${major_hex}" in ''|*[!0-9a-fA-F]*) exit 1 ;; esac
  case "${minor_hex}" in ''|*[!0-9a-fA-F]*) exit 1 ;; esac
  major=$((0x${major_hex}))
  minor=$((0x${minor_hex}))
  [ "${major}" -eq 226 ] && [ "${minor}" -ge 128 ] && [ "${minor}" -le 255 ] || exit 1
  printf '%s\n' "${minor}"
)

if ! minor=$(render_minor "${runtime_device}"); then
  echo "invalid iGPU device: ${runtime_device}; expected a DRM render character device" >&2
  exit 1
fi

canonical_device=${device_root}/renderD${minor}
if [ -e "${canonical_device}" ] || [ -L "${canonical_device}" ]; then
  if ! existing_minor=$(render_minor "${canonical_device}") || [ "${existing_minor}" != "${minor}" ]; then
    echo "conflicting iGPU render path: ${canonical_device}" >&2
    exit 1
  fi
else
  ln -s thoughtloud-igpu "${canonical_device}"
fi

exec "$@"
