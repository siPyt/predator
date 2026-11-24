#!/bin/bash
# Auto-export Predator's discovered CSV, Excel point map, Mirius .icsproj,
# recon CSV, and snapshots onto a newly inserted FAT32/exFAT/NTFS USB stick.
# Invoked by /etc/udev/rules.d/99-predator-usb-export.rules via systemd-run.
set -euo pipefail

DEV="${1:-}"
[ -n "$DEV" ] || exit 0
[ -b "$DEV" ] || exit 0

USER_NAME="admin"
USER_HOME="/home/${USER_NAME}"
FSTYPE="$(blkid -o value -s TYPE "$DEV" 2>/dev/null || true)"
case "$FSTYPE" in
  vfat|exfat|ntfs) ;;
  *) exit 0 ;;
esac

sleep 2

# Reuse desktop automount if already mounted; otherwise mount temporarily.
EXISTING_MNT="$(findmnt -rn -o TARGET "$DEV" 2>/dev/null | head -n1 || true)"
WE_MOUNTED=0
if [ -n "$EXISTING_MNT" ]; then
  MNT="$EXISTING_MNT"
else
  MNT="/run/predator-usb-export/$(basename "$DEV")"
  mkdir -p "$MNT"
  mount -o rw,utf8,umask=000 "$DEV" "$MNT" 2>/dev/null || mount -o rw "$DEV" "$MNT"
  WE_MOUNTED=1
fi

STAMP="$(date +%Y-%m-%d_%H%M%S)"
DEST="${MNT}/predator_${STAMP}"
mkdir -p "$DEST"

copy_if_exists() {
  [ -f "$1" ] && cp -f "$1" "$DEST/$(basename "$1")" || true
}

copy_if_exists "${USER_HOME}/Desktop/predator_discovered.csv"
copy_if_exists "${USER_HOME}/predator_discovered.csv"
copy_if_exists "${USER_HOME}/Desktop/Predator_Points.xlsx"
copy_if_exists "${USER_HOME}/Desktop/Predator_Mirius_BACnet.icsproj"
copy_if_exists "${USER_HOME}/predator_recon.csv"

if [ -d "${USER_HOME}/predator_snapshots" ]; then
  mkdir -p "${DEST}/snapshots"
  cp -f "${USER_HOME}/predator_snapshots/"*.json "${DEST}/snapshots/" 2>/dev/null || true
fi

{
  echo "Predator Field Export"
  echo "Timestamp: $(date -Is)"
  echo "Hostname:  $(hostname)"
  echo "Device:    ${DEV} (${FSTYPE})"
  echo "Files:"
  ls -la "$DEST"
} > "${DEST}/EXPORT_INFO.txt"

sync

if [ "$WE_MOUNTED" -eq 1 ]; then
  umount "$MNT" || true
  rmdir "$MNT" 2>/dev/null || true
fi

logger -t predator-usb-export "Exported Predator files to ${DEV} -> predator_${STAMP}"

# Blink the Pi green ACT LED 5 times to signal "export complete, safe to unplug"
LED="/sys/class/leds/ACT/brightness"
if [ -w "$LED" ]; then
  for _ in 1 2 3 4 5; do
    echo 1 > "$LED" 2>/dev/null || true
    sleep 0.15
    echo 0 > "$LED" 2>/dev/null || true
    sleep 0.15
  done
fi

# Best-effort desktop toast if admin has an active Wayland session
if command -v notify-send >/dev/null 2>&1; then
  su - "$USER_NAME" -c "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus notify-send 'Predator USB Export' 'Saved predator_${STAMP} to USB drive — safe to unplug.'" 2>/dev/null || true
fi
