#!/bin/bash
# install.sh - set up predator-gateway on the Pi: venv, dependencies, config
# files, the systemd service, and the desktop CSV viewer's autostart entry.
#
# Run from inside the repo checkout on the Pi, e.g.:
#   cd ~/predator && bash scripts/install.sh
#
# Needs internet the first time (pip install). If predator has none yet, pip
# download the packages in requirements.txt on a machine that does, transfer
# the wheel files over, and run `pip install --no-index --find-links <dir> -r
# requirements.txt` inside the venv this script creates instead.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${VENV_DIR:-$HOME/predator-venv}"

echo "== predator: install from $REPO_DIR =="

echo "-- venv at $VENV_DIR --"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$REPO_DIR/requirements.txt"

echo "-- config files in \$HOME (only if missing - won't overwrite your edits) --"
[ -f "$HOME/predator_gateway.conf" ] || cp "$REPO_DIR/config/predator_gateway.conf.example" "$HOME/predator_gateway.conf"
[ -f "$HOME/predator_points.json" ] || cp "$REPO_DIR/config/predator_points.json.example" "$HOME/predator_points.json"

echo "-- dialout group (needed to open /dev/ttyUSB*/ttyACM* without root) --"
if ! groups "$USER" | grep -qw dialout; then
  sudo usermod -aG dialout "$USER"
  echo "   added $USER to dialout - log out/in (or reboot) once for this to take effect"
fi

echo "-- systemd service --"
sudo cp "$REPO_DIR/systemd/predator-gateway.service" /etc/systemd/system/predator-gateway.service
sudo sed -i "s#{{VENV_DIR}}#$VENV_DIR#g; s#{{REPO_DIR}}#$REPO_DIR#g; s#{{USER}}#$USER#g" \
  /etc/systemd/system/predator-gateway.service
sudo systemctl daemon-reload
sudo systemctl enable predator-gateway.service

echo "-- desktop CSV viewer autostart (runs when you log into the Pi's GUI / over VNC) --"
mkdir -p "$HOME/.config/autostart"
sed "s#{{VENV_DIR}}#$VENV_DIR#g; s#{{REPO_DIR}}#$REPO_DIR#g" \
  "$REPO_DIR/systemd/predator-viewer.desktop.in" > "$HOME/.config/autostart/predator-viewer.desktop"

echo "== done =="
echo "Start the gateway now with:   sudo systemctl start predator-gateway"
echo "Watch it with:                journalctl -u predator-gateway -f"
echo "Status/CSV download API on:   http://<predator-ip>:8099/"
echo "The CSV viewer window appears next time you log into the Pi's desktop"
echo "(or VNC in now and run: $VENV_DIR/bin/python -m gateway.viewer)"
