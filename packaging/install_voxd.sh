#!/usr/bin/env bash
# Compatibility entry point for existing installer instructions.
set -euo pipefail
exec "$(dirname -- "${BASH_SOURCE[0]}")/install_thoughtloud.sh" "$@"
