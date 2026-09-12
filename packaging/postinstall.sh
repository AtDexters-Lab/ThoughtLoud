#!/bin/sh
set -e

# System integration only. Python and Python packages are already in the bundle.
getent group input >/dev/null 2>&1 || groupadd input
if command -v udevadm >/dev/null 2>&1; then
  udevadm control --reload-rules || echo "udev reload deferred until reboot"
fi
if command -v modprobe >/dev/null 2>&1; then
  modprobe uinput || echo "uinput load deferred until reboot"
fi
if [ ! -f /etc/modules-load.d/uinput.conf ]; then
  mkdir -p /etc/modules-load.d
  echo uinput > /etc/modules-load.d/uinput.conf
fi
if command -v udevadm >/dev/null 2>&1; then
  udevadm trigger --subsystem-match=misc --sysname-match=uinput || echo "Input rule activation deferred until reboot"
fi
echo "ThoughtLoud installed. Open ThoughtLoud from the application menu to configure it."
