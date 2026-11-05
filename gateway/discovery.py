"""Auto-detect what's plugged into predator's serial ports, continuously.

The user doesn't tell predator whether a port has BACnet MS/TP or Modbus RTU
on it, or at what baud rate - this figures that out:

1. Enumerate whatever /dev/ttyUSB*/ttyACM* (USB-to-RS485 adapters) currently
   exist (config [autodetect] port_glob).
2. For each port predator hasn't already confirmed, try BACnet MS/TP first at
   every candidate baud: a Who-Is broadcast via the bacnet-stack CLI tools
   (gateway/mstp.py) is cheap, protocol-correct (handles MS/TP token passing
   properly instead of us hand-rolling frame sniffing), and if ANY device
   answers, that baud is confirmed MS/TP.
3. If nothing answers as MS/TP at any baud, Modbus RTU has no "idle chatter"
   to listen for (it's purely master-initiated) - so this ACTIVELY probes: a
   quick pass tries a handful of common unit ids at each baud, and only if
   that also finds nothing does a slower full 1..N unit sweep run. Any valid
   Modbus response (including an exception reply - that still has a valid
   CRC, so it still proves "something Modbus RTU-capable is here") confirms
   that baud.
4. Whatever is found gets written to gateway/runtime_state.py, which is what
   the points-map poller / CSV export / HTTP API all read from - NOT the
   static config file.

Already-confirmed ports are left alone most of the time, but get a cheap
periodic re-check so a swapped cable/adapter is noticed without a restart.
"""

import glob
import logging
import time

from . import config as cfgmod
from . import mstp
from . import modbus_rtu
from .runtime_state import STATE, MSTP, MODBUS_RTU, UNKNOWN

log = logging.getLogger("predator.discovery")

# How many confirmed-port re-checks happen between full unknown-port rescans.
_REVERIFY_EVERY_N_LOOPS = 4


def find_serial_ports(cfg):
    ports = []
    for pattern in cfgmod.serial_port_globs(cfg):
        ports.extend(glob.glob(pattern))
    return sorted(set(ports))


def _detect_mstp(cfg, port, bauds, timeout):
    for baud in bauds:
        result = mstp.probe_port(cfg, port, baud, timeout=timeout)
        if result["devices"]:
            return baud, result["devices"]
    return None, []


def _modbus_unit_sweep(port, baud, max_unit, timeout, seed=None):
    found = {seed} if seed else set()
    for unit in range(1, max_unit + 1):
        if unit in found:
            continue
        if modbus_rtu.probe_port(port, baud, unit, timeout=timeout):
            found.add(unit)
    return sorted(found)


def _detect_modbus(cfg, port, bauds, quick_units, deep_max_unit, timeout):
    # Quick pass: common unit ids at each baud - fast, finds the typical case.
    for baud in bauds:
        for unit in quick_units:
            if modbus_rtu.probe_port(port, baud, unit, timeout=timeout):
                # Confirmed this baud - now enumerate every unit on it.
                return baud, _modbus_unit_sweep(port, baud, deep_max_unit, timeout, seed=unit)
    # Deep pass: nothing common responded anywhere - sweep fully before giving up.
    for baud in bauds:
        devices = _modbus_unit_sweep(port, baud, deep_max_unit, timeout)
        if devices:
            return baud, devices
    return None, []


def detect_port(cfg, port):
    """Run one full auto-detect pass on `port` and update runtime_state."""
    bauds = cfgmod.autodetect_scan_bauds(cfg)
    mstp_timeout = cfgmod.autodetect_mstp_probe_timeout(cfg)
    modbus_timeout = cfgmod.autodetect_modbus_probe_timeout(cfg)
    quick_units = cfgmod.autodetect_modbus_quick_units(cfg)
    deep_max_unit = cfgmod.autodetect_modbus_deep_max_unit(cfg)

    log.info("scanning %s ...", port)

    if cfgmod.getboolean(cfg, "mstp", "enabled"):
        baud, devices = _detect_mstp(cfg, port, bauds, mstp_timeout)
        if devices:
            STATE.set_port_state(
                port, protocol=MSTP, baud=baud, devices=devices,
                last_scan=time.time(), last_confirmed=time.time(),
                note=f"{len(devices)} MS/TP device(s)",
            )
            log.info("%s: BACnet MS/TP @ %s baud, devices=%s", port, baud, devices)
            return {"port": port, "protocol": MSTP, "baud": baud, "devices": devices}

    if cfgmod.getboolean(cfg, "modbus", "enabled"):
        baud, devices = _detect_modbus(cfg, port, bauds, quick_units, deep_max_unit, modbus_timeout)
        if devices:
            STATE.set_port_state(
                port, protocol=MODBUS_RTU, baud=baud, devices=devices,
                last_scan=time.time(), last_confirmed=time.time(),
                note=f"{len(devices)} Modbus RTU unit(s)",
            )
            log.info("%s: Modbus RTU @ %s baud, units=%s", port, baud, devices)
            return {"port": port, "protocol": MODBUS_RTU, "baud": baud, "devices": devices}

    STATE.set_port_state(
        port, protocol=UNKNOWN, baud=0, devices=[], last_scan=time.time(),
        note="no MS/TP or Modbus RTU response at any baud",
    )
    log.info("%s: nothing identified at any baud", port)
    return {"port": port, "protocol": UNKNOWN, "baud": None, "devices": []}


def _reverify(cfg, state):
    """Cheap re-check that a confirmed port still carries what we think it
    does. Downgrades to UNKNOWN (triggering a full rescan next loop) if not -
    this is what notices a swapped adapter/cable without needing a restart.
    """
    port, baud = state.port, state.baud
    if state.protocol == MSTP:
        result = mstp.probe_port(cfg, port, baud, timeout=cfgmod.autodetect_mstp_probe_timeout(cfg))
        still_there = bool(result["devices"])
    elif state.protocol == MODBUS_RTU:
        units = state.devices or [1]
        still_there = any(
            modbus_rtu.probe_port(port, baud, u, timeout=cfgmod.autodetect_modbus_probe_timeout(cfg))
            for u in units
        )
    else:
        return
    if still_there:
        state.last_confirmed = time.time()
    else:
        log.info("%s: %s no longer responding at %s baud - will rescan", port, state.protocol, baud)
        STATE.set_port_state(port, protocol=UNKNOWN, baud=0, devices=[], last_scan=time.time())


def force_rescan(cfg, port=None):
    """Used by the HTTP API's rescan trigger. `port=None` rescans everything."""
    ports = [port] if port else find_serial_ports(cfg)
    results = []
    for p in ports:
        STATE.set_port_state(p, protocol=UNKNOWN, baud=0, devices=[])
        results.append(detect_port(cfg, p))
    return results


def run_loop(cfg, stop_event):
    """Background thread body: scan unknown ports, periodically re-verify
    confirmed ones, and drop state for anything unplugged. Runs until
    `stop_event` is set.
    """
    rescan_seconds = cfgmod.autodetect_rescan_seconds(cfg)
    known_ports = set()
    loop_count = 0

    while not stop_event.is_set():
        loop_count += 1
        try:
            current_ports = set(find_serial_ports(cfg))
        except Exception:  # noqa: BLE001
            log.exception("error listing serial ports")
            current_ports = set()

        for gone in known_ports - current_ports:
            log.info("%s unplugged", gone)
            STATE.remove_port(gone)
        known_ports = current_ports

        for port in sorted(current_ports):
            state = STATE.get_port_state(port)
            try:
                if state is None or state.protocol == UNKNOWN:
                    detect_port(cfg, port)
                elif loop_count % _REVERIFY_EVERY_N_LOOPS == 0:
                    _reverify(cfg, state)
            except Exception:  # noqa: BLE001
                log.exception("error auto-detecting %s", port)

        stop_event.wait(rescan_seconds)
