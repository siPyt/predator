#!/bin/bash
# Install Predator's USB stick auto-export script and udev rule on the Pi.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

sudo install -m 755 "${SCRIPT_DIR}/usb-export.sh" /usr/local/sbin/predator-usb-export.sh
sudo sed -i 's/\r$//' /usr/local/sbin/predator-usb-export.sh
sudo install -m 644 "${SCRIPT_DIR}/99-predator-usb-export.rules" /etc/udev/rules.d/99-predator-usb-export.rules
sudo sed -i 's/\r$//' /etc/udev/rules.d/99-predator-usb-export.rules
sudo udevadm control --reload-rules
echo "Installed predator-usb-export udev rule. Plug in any FAT32/exFAT USB stick to auto-export."
