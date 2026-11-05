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


def _modbus_unit_sweep(port, baud, max_unit, timeout, seed=None, parity="N", stopbits=1, bytesize=8):
    found = {seed} if seed else set()
    for unit in range(1, max_unit + 1):
        if unit in found:
            continue
        if modbus_rtu.probe_port(port, baud, unit, parity=parity, stopbits=stopbits, bytesize=bytesize, timeout=timeout):
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


def _dump_mstp_devices(cfg, port, baud, devices):
    """Enumerate each found MS/TP device's object list (bacepics) - this is
    the actual BACnet object type/instance/name a BMS integrator needs to
    configure a point, not just "device 5 exists". Best-effort: a device that
    fails to dump still ends up in the CSV, just without a per-object
    breakdown (see csv_export.py's fallback)."""
    device_objects = {}
    for inst in devices:
        try:
            result = mstp.dump_device(cfg, inst, iface=port, baud=baud)
            device_objects[inst] = result.get("objects") or []
            log.info("%s: device %s -> %d object(s)", port, inst, len(device_objects[inst]))
        except Exception:  # noqa: BLE001
            log.exception("error dumping object list for MS/TP device %s on %s", inst, port)
            device_objects[inst] = []
    return device_objects


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
            device_objects = _dump_mstp_devices(cfg, port, baud, devices)
            STATE.set_port_state(
                port, protocol=MSTP, baud=baud, devices=devices, device_objects=device_objects,
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


def detect_port_forced(cfg, port, protocol, baud, parity="N", stopbits=1, bytesize=8):
    """Apply a manual troubleshooting override directly - skips the baud/
    protocol sweep entirely and just uses what the explorer GUI was told to
    use. Still runs the same enrichment as normal auto-detect (Who-Is +
    bacepics for MS/TP, a unit sweep at that one baud for Modbus) so the CSV
    stays populated even when auto-detect itself didn't find it (e.g. a
    non-standard baud outside the sweep list, or a flaky device)."""
    mstp_timeout = cfgmod.autodetect_mstp_probe_timeout(cfg)
    modbus_timeout = cfgmod.autodetect_modbus_probe_timeout(cfg)
    deep_max_unit = cfgmod.autodetect_modbus_deep_max_unit(cfg)

    if protocol == MSTP:
        result = mstp.probe_port(cfg, port, baud, timeout=mstp_timeout)
        devices = result["devices"]
        device_objects = _dump_mstp_devices(cfg, port, baud, devices) if devices else {}
        STATE.set_port_state(
            port, protocol=MSTP, baud=baud, parity=parity, stopbits=stopbits, bytesize=bytesize,
            devices=devices, device_objects=device_objects, last_scan=time.time(),
            last_confirmed=time.time() if devices else 0.0,
            note=f"forced MS/TP @ {baud}" + ("" if devices else " - no response yet, check wiring/baud"),
        )
        log.info("%s: forced MS/TP @ %s baud, devices=%s", port, baud, devices)
        return {"port": port, "protocol": MSTP, "baud": baud, "devices": devices}

    if protocol == MODBUS_RTU:
        quick_units = cfgmod.autodetect_modbus_quick_units(cfg)
        devices = [
            u for u in quick_units
            if modbus_rtu.probe_port(port, baud, u, parity=parity, stopbits=stopbits, bytesize=bytesize, timeout=modbus_timeout)
        ]
        if not devices:
            devices = _modbus_unit_sweep(
                port, baud, deep_max_unit, modbus_timeout, parity=parity, stopbits=stopbits, bytesize=bytesize,
            )
        STATE.set_port_state(
            port, protocol=MODBUS_RTU, baud=baud, parity=parity, stopbits=stopbits, bytesize=bytesize,
            devices=devices, last_scan=time.time(), last_confirmed=time.time() if devices else 0.0,
            note=f"forced Modbus RTU @ {baud}" + ("" if devices else " - no response yet, check wiring/baud/parity"),
        )
        log.info("%s: forced Modbus RTU @ %s baud, units=%s", port, baud, devices)
        return {"port": port, "protocol": MODBUS_RTU, "baud": baud, "devices": devices}

    raise ValueError(f"unsupported forced protocol {protocol!r}")


def _reverify(cfg, state):
    """Cheap re-check that a confirmed port still carries what we think it
    does. Downgrades to UNKNOWN (triggering a full rescan next loop) if not -
    this is what notices a swapped adapter/cable without needing a restart.
    A port with an active manual override never gets downgraded to UNKNOWN
    here (that would silently abandon the override) - it just gets a note
    that it's not currently responding, and stays pinned until the user
    clears the override themselves.
    """
    port, baud = state.port, state.baud
    override = STATE.get_override(port)
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
    elif override is not None:
        state.note = f"forced {state.protocol} @ {baud} - not responding right now"
        state.last_scan = time.time()
    else:
        log.info("%s: %s no longer responding at %s baud - will rescan", port, state.protocol, baud)
        STATE.set_port_state(port, protocol=UNKNOWN, baud=0, devices=[], last_scan=time.time())


def force_rescan(cfg, port=None):
    """Used by the HTTP API's rescan trigger. `port=None` rescans everything.
    A port with an active manual override gets that override re-applied
    instead of reverting to auto-detect - "rescan" should not silently
    discard a troubleshooting override."""
    ports = [port] if port else find_serial_ports(cfg)
    results = []
    for p in ports:
        override = STATE.get_override(p)
        if override is not None:
            results.append(detect_port_forced(
                cfg, p, override.protocol, override.baud,
                parity=override.parity, stopbits=override.stopbits, bytesize=override.bytesize,
            ))
        else:
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
            override = STATE.get_override(port)
            try:
                if override is not None:
                    # A new/changed override takes effect immediately rather
                    # than waiting for the next periodic re-verify.
                    if state is None or state.protocol != override.protocol or state.baud != override.baud:
                        detect_port_forced(
                            cfg, port, override.protocol, override.baud,
                            parity=override.parity, stopbits=override.stopbits, bytesize=override.bytesize,
                        )
                    elif loop_count % _REVERIFY_EVERY_N_LOOPS == 0:
                        _reverify(cfg, state)
                elif state is None or state.protocol == UNKNOWN:
                    detect_port(cfg, port)
                elif loop_count % _REVERIFY_EVERY_N_LOOPS == 0:
                    _reverify(cfg, state)
            except Exception:  # noqa: BLE001
                log.exception("error auto-detecting %s", port)

        stop_event.wait(rescan_seconds)
