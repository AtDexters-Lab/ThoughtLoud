#!/bin/sh
set -e

getent group input >/dev/null 2>&1 || groupadd input || true

if [ -f /etc/udev/rules.d/99-uinput.rules ]; then
  udevadm control --reload-rules || true
  udevadm trigger || true
fi

modprobe uinput || true
if [ ! -f /etc/modules-load.d/uinput.conf ]; then
  echo uinput > /etc/modules-load.d/uinput.conf 2>/dev/null || true
fi

if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ]; then
  usermod -aG input "$SUDO_USER" 2>/dev/null || true
fi

APPDIR=/opt/voxd
PY=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3.9 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    PY="$candidate"
    break
  fi
done

if [ -n "$PY" ]; then
  "$PY" -m venv --system-site-packages "$APPDIR/.venv" >/dev/null 2>&1 || true
  if [ -x "$APPDIR/.venv/bin/python" ]; then
    "$APPDIR/.venv/bin/python" -m pip install --disable-pip-version-check --no-input \
      "sounddevice>=0.5" "PyQt6>=6.5" "platformdirs>=4.2" "PyYAML>=6.0" \
      "pyperclip>=1.8" "numpy>=1.26" "requests>=2.28" >/dev/null 2>&1 || true
  fi
fi

if [ ! -x "$APPDIR/.venv/bin/python" ]; then
  echo "voxd installation failed: could not create the Python environment" >&2
  exit 1
fi
if ! "$APPDIR/.venv/bin/python" -m pip install --disable-pip-version-check --no-input \
  "onnxruntime>=1.16.1" >/dev/null 2>&1; then
  echo "voxd installation failed: could not install ONNX Runtime" >&2
  exit 1
fi
if ! ORT_DISABLE_TELEMETRY=1 "$APPDIR/.venv/bin/python" -c "import onnxruntime" >/dev/null 2>&1; then
  echo "voxd installation failed: ONNX Runtime is unavailable" >&2
  exit 1
fi

echo "voxd installed. Log out once if input-group membership changed, then run: voxd --setup"
