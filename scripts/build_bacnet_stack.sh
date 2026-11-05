#!/bin/bash
# build_bacnet_stack.sh - build Steve Karg's bacnet-stack CLI tools
# (bacrp/bacwp/bacwi/bacepics) that gateway/mstp.py drives via subprocess, for
# BACnet MS/TP - a pure-Python implementation can't reliably meet MS/TP's
# token-passing timing, so predator talks to the MS/TP trunk through these
# compiled tools instead (see gateway/mstp.py's module docstring).
#
# Generic/standalone: no PAMS paths or identity strings, just the upstream
# project built into ~/bacnet-stack/bin (matching gateway/config.py's default
# [mstp] bacnet_bin path).
#
# NEEDS INTERNET the first time it runs (apt + git clone). If predator has no
# internet access when you run this, clone/download bacnet-stack on a machine
# that does, transfer the source tree over (scp/rsync), and re-run this script
# with REPO_DIR pointing at that transferred copy - everything after the
# "clone if missing" step only needs a local C toolchain, which Raspberry Pi
# OS Desktop already includes.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/bacnet-stack/bacnet-stack.git}"
REPO_DIR="${REPO_DIR:-$HOME/bacnet-stack-src}"
INSTALL_BIN="${INSTALL_BIN:-$HOME/bacnet-stack/bin}"
TOOLS=(bacrp bacwp bacwi bacepics)

echo "== predator: build bacnet-stack =="

if ! command -v gcc >/dev/null 2>&1 || ! command -v make >/dev/null 2>&1; then
  echo "-- installing build prerequisites (needs internet/apt) --"
  sudo apt-get update
  sudo apt-get install -y build-essential git
fi

if [ ! -d "$REPO_DIR" ]; then
  echo "-- cloning $REPO_URL into $REPO_DIR --"
  git clone --depth 1 "$REPO_URL" "$REPO_DIR"
else
  echo "-- using existing source at $REPO_DIR --"
fi

cd "$REPO_DIR"

echo "-- building apps (trying BACDL=mstp first, then a plain build) --"
if ! make BACDL=mstp apps 2>build_mstp.log; then
  echo "   BACDL=mstp apps failed, see $REPO_DIR/build_mstp.log - trying plain 'make apps'"
  make apps
fi

echo "-- collecting binaries into $INSTALL_BIN --"
mkdir -p "$INSTALL_BIN"
missing=()
for tool in "${TOOLS[@]}"; do
  found="$(find "$REPO_DIR" -maxdepth 4 -type f -name "$tool" -perm -u+x 2>/dev/null | head -n1)"
  if [ -n "$found" ]; then
    cp "$found" "$INSTALL_BIN/$tool"
  else
    missing+=("$tool")
  fi
done

if [ "${#missing[@]}" -eq 0 ]; then
  echo "== done: all of ${TOOLS[*]} are in $INSTALL_BIN =="
else
  echo "== WARNING: could not find these after building: ${missing[*]} =="
  echo "   Check $REPO_DIR/build_mstp.log and the repo's apps/ directory structure -"
  echo "   bacnet-stack's exact Makefile targets/output paths vary by version, this"
  echo "   script may need its 'make' invocation adjusted for the version that cloned."
  exit 1
fi
