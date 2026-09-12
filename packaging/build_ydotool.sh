#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 2 ]]; then
  echo "usage: $0 PINNED_YDOTOOL_SOURCE NEW_OUTPUT_DIR" >&2
  exit 2
fi
source_repo=$(realpath "$1")
output_dir=$(realpath -m "$2")
revision=57ba7d0af525e82da2de0e275d169477f293b197
[[ ! -e "$output_dir" ]] || { echo "Output already exists" >&2; exit 1; }
[[ "$(git -C "$source_repo" rev-parse HEAD)" == "$revision" ]] || {
  echo "Expected ydotool v1.0.4 at $revision" >&2; exit 1;
}
[[ -z "$(git -C "$source_repo" status --porcelain)" ]] || {
  echo "Source must be clean" >&2; exit 1;
}
mkdir -p "$output_dir/libexec" "$output_dir/licenses"
cd "$source_repo"
cmake -S . -B "$output_dir/build" -DCMAKE_BUILD_TYPE=Release
cmake --build "$output_dir/build" --target ydotool ydotoold -j2
cp "$output_dir/build/ydotool" "$output_dir/build/ydotoold" "$output_dir/libexec/"
cp LICENSE "$output_dir/licenses/LICENSE.ydotool"
git archive --format=tar.gz --prefix=ydotool-1.0.4/ "$revision" > "$output_dir/licenses/ydotool-source.tar.gz"
