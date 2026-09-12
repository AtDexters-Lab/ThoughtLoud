#!/usr/bin/env bash
# Install a downloaded ThoughtLoud .deb or .rpm using the host package manager.
set -euo pipefail
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
  echo 'Usage: install_thoughtloud.sh [path/to/thoughtloud.deb|thoughtloud.rpm]'
  exit 0
fi
package_path="${1:-}"
if [[ -z "$package_path" ]]; then
  shopt -s nullglob
  candidates=(thoughtloud_*.deb thoughtloud-*.rpm)
  if (( ${#candidates[@]} != 1 )); then
    echo 'Specify one ThoughtLoud .deb or .rpm package path.' >&2
    exit 1
  fi
  package_path="${candidates[0]}"
fi
if [[ ! -f "$package_path" ]]; then
  echo "Package not found: $package_path" >&2
  exit 1
fi
package_path="$(cd -- "$(dirname -- "$package_path")" && pwd)/$(basename -- "$package_path")"
case "$package_path" in
  *.deb)
    command -v apt >/dev/null || { echo 'This package requires an apt-based system.' >&2; exit 1; }
    sudo apt install "$package_path"
    ;;
  *.rpm)
    if command -v dnf5 >/dev/null; then
      sudo dnf5 install "$package_path"
    elif command -v dnf >/dev/null; then
      sudo dnf install "$package_path"
    elif command -v zypper >/dev/null; then
      sudo zypper install "$package_path"
    else
      echo 'This package requires dnf or zypper.' >&2
      exit 1
    fi
    ;;
  *) echo 'Expected a .deb or .rpm package.' >&2; exit 1 ;;
esac
echo 'ThoughtLoud installed. Open ThoughtLoud from the application menu.'
