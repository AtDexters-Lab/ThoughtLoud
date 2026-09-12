#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

uninstall_config_home="${XDG_CONFIG_HOME:-$HOME/.config}"
uninstall_data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
[[ "$uninstall_config_home" == /* ]] || uninstall_config_home="$HOME/.config"
[[ "$uninstall_data_home" == /* ]] || uninstall_data_home="$HOME/.local/share"

systemctl --user disable --now voxd-tray.service 2>/dev/null || true

rm -f "$uninstall_config_home/autostart/voxd-tray.desktop"
rm -f "$HOME/.config/systemd/user/voxd-tray.service"
rm -f "$uninstall_data_home/applications/voxd-tray.desktop"
rm -f "$uninstall_data_home/icons/hicolor/256x256/apps/voxd.png"
rm -f "$uninstall_data_home/icons/hicolor/scalable/apps/thoughtloud.svg"
rm -rf "$REPO_DIR/.venv"
systemctl --user daemon-reload 2>/dev/null || true

echo "ThoughtLoud source install removed. Config and recordings were kept."
