#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 0 || ${EUID} -ne 0 ]]; then
  echo "usage: sudo bash $0" >&2
  exit 2
fi
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
sources=(70-thoughtloud-igpu.rules thoughtloud-igpu@.service start-container.sh)
destinations=(/etc/udev/rules.d/70-thoughtloud-igpu.rules /etc/systemd/system/thoughtloud-igpu@.service /usr/local/libexec/thoughtloud-igpu-start)
modes=(0644 0644 0755)

# This host prerequisite is separate from the create-only runtime installer.
# Refuse conflicting files before installing anything; reruns of the same
# preparation are harmless. It neither creates nor starts a container.
for i in "${!sources[@]}"; do
  source_file="${script_dir}/${sources[i]}"
  destination="${destinations[i]}"
  [[ -f "${source_file}" ]] || { echo "missing ${source_file}" >&2; exit 1; }
  if [[ -e "${destination}" || -L "${destination}" ]]; then
    if [[ -L "${destination}" ]] || ! cmp --silent "${source_file}" "${destination}"; then
      echo "conflicting existing file: ${destination}; review it before replacing" >&2
      exit 1
    fi
  fi
done
for i in "${!sources[@]}"; do
  install -D -o root -g root -m "${modes[i]}" "${script_dir}/${sources[i]}" "${destinations[i]}"
done
systemctl daemon-reload
udevadm control --reload-rules
udevadm trigger --action=change --subsystem-match=drm --sysname-match='renderD*'
udevadm settle --timeout=15
echo "GPU alias and startup service installed. After creating the container, enable its thoughtloud-igpu@CONTAINER_NAME.service instance."
