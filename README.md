# predator

**Looking to just use predator day-to-day (plugging in wiring, viewing
results, getting the CSV)? See [USER_GUIDE.md](./USER_GUIDE.md) instead -
this README is the technical/developer reference.**

A standalone industrial protocol gateway running on a Raspberry Pi 5: it
auto-senses whatever is plugged in - BACnet/IP on the Ethernet (RJ45) port, or
BACnet MS/TP / Modbus RTU on a USB-to-RS485 adapter - figures out the protocol
and baud rate on its own, republishes everything it finds as BACnet/IP objects
on one virtual device, and writes a live CSV of everything discovered.

This is a standalone project, not PAMS and not the Electron "Predator" app
that happens to live inside the PAMS repo - some BACnet/Modbus *techniques*
were reused from PAMS's proven field deployment, nothing else.

## What it does

1. **Ethernet (RJ45):** runs a BACnet/IP virtual device and sends a periodic
   global Who-Is. Any BACnet/IP device that answers gets recorded - no setup
   needed, this is just normal BACnet/IP discovery.
2. **USB-to-RS485:** you don't tell it what's plugged in. For every
   `/dev/ttyUSB*`/`/dev/ttyACM*` port found, it sweeps common baud rates and
   tries BACnet MS/TP first (a Who-Is broadcast via the bacnet-stack CLI
   tools), then Modbus RTU (active register probes against a sweep of unit
   ids) if nothing answered as MS/TP. Whatever's found - protocol, baud,
   device/unit ids - gets tracked automatically; swap the cable later and it
   re-detects within `[autodetect] rescan_seconds`.
3. Everything discovered, plus any points you've curated into
   `~/predator_points.json` (see `config/predator_points.json.example`) with
   their live values, is written to a CSV every few seconds
   (`[csv] path`/`desktop_copy`).
4. **See it on the Pi's screen (over VNC or a monitor):** `gateway/viewer.py`
   is a small always-on-top table window that re-reads that CSV every few
   seconds - no spreadsheet app needed/installed.
5. **Get the CSV onto your PC:** browse to
   `http://<predator-ip>:8099/export.csv` (or `curl -O`) - no file-transfer
   setup required. `http://<predator-ip>:8099/status` and `/points.json` are
   also there for live JSON state.

## Why "gateway", not a true BACnet router

A true BACnet/IP <-> MS/TP *router* means implementing MS/TP's token-passing
in pure Python, which can't reliably meet MS/TP's strict serial timing (this
is exactly why the MS/TP side here shells out to the compiled bacnet-stack
CLI tools instead of using bacpypes' own MS/TP implementation). Instead,
predator polls MS/TP and Modbus RTU and re-publishes what it finds as BACnet
objects on its own local BACnet/IP device - the standard, realistically
reliable "protocol gateway" pattern.

## Layout

```
gateway/
  config.py        - loads ~/predator_gateway.conf (INI), all keys optional
  runtime_state.py - thread-safe "what have we found so far" shared state
  discovery.py      - serial auto-detect engine (MS/TP vs Modbus RTU + baud)
  mstp.py           - BACnet MS/TP via the bacnet-stack CLI tools
  modbus_rtu.py     - Modbus RTU via pymodbus
  bacnet_ip.py      - virtual BACnet/IP device + Who-Is/I-Am discovery
  points.py         - curated points-map model (field point -> BACnet object)
  csv_export.py     - writes the discovered-points CSV
  api.py            - status/CSV-download HTTP API (stdlib only, port 8099)
  viewer.py         - Tkinter CSV viewer for the Pi's own desktop
  service.py        - systemd entrypoint wiring all of the above together
scripts/
  build_bacnet_stack.sh  - builds bacrp/bacwp/bacwi/bacepics from source
  install.sh             - venv + pip install + systemd + autostart setup
  eth0-stable-watch.sh   - unrelated Ethernet-stability fix, see sofar.txt
systemd/
  predator-gateway.service       - the main service
  predator-viewer.desktop.in     - autostart template for the CSV viewer
  eth0-stable-watch.service      - the Ethernet-stability fix's service
config/
  predator_gateway.conf.example
  predator_points.json.example
```

## Install on the Pi

```bash
git clone https://github.com/siPyt/predator.git ~/predator
cd ~/predator
bash scripts/build_bacnet_stack.sh   # needs internet once, or see its own notes
bash scripts/install.sh              # venv, pip deps, config, systemd, autostart
sudo systemctl start predator-gateway
journalctl -u predator-gateway -f
```

If the Pi has no internet access yet: `pip download -r requirements.txt -d
<dir>` on a machine that does, transfer `<dir>` over, then `pip install
--no-index --find-links <dir> -r requirements.txt` inside the venv
`scripts/install.sh` created. Same idea for `bacnet-stack` - clone/download
its source elsewhere and transfer the source tree, then run
`scripts/build_bacnet_stack.sh` with `REPO_DIR` pointing at it (building
itself only needs a local C toolchain, which the Pi already has).

## Status

Auto-detect, CSV export, the HTTP API, and the Tkinter viewer are written and
pass local syntax/import/integration checks (including actually booting
`gateway/service.py` end-to-end against the real `pymodbus`/`bacpypes3`
packages and confirming the API responds) - **not yet tested on the Pi
itself against real MS/TP/Modbus RTU/BACnet-IP hardware**, which is the next
step once predator's network is confirmed stable (see sofar.txt) and its
actual Python/package/USB environment has been checked.

Note on BACnet/IP: this uses `bacpypes3`, not classic `bacpypes` - classic
bacpypes depends on Python's `asyncore`/`asynchat` modules, which were
removed in Python 3.12, and Raspberry Pi OS's current Python is 3.12+/Debian
trixie's 3.13, so classic bacpypes cannot even import there. This was caught
and fixed by actually installing both packages locally and trying them before
deploying, not assumed - see sofar.txt Chapter 5's update for what else was
verified the same way (the `--address`/vendor-id/`who_is()`/object-lookup
quirks `gateway/bacnet_ip.py`'s comments call out). Per-device BACnet/IP
object-list enumeration (vs. just recording that a device exists) was
deliberately left out of this pass as a lower-risk follow-up.

## Multi-protocol scanner

A separate one-shot scanner (MQTT, PROFINET, PROFIBUS, Modbus TCP, EtherNet/IP, S7, OPC UA, KNX, passive MS/TP capture, ...) lives in the `predator/` package; see [SCANNER.md](./SCANNER.md).
