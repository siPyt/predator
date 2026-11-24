"""BACnet MS/TP access via Steve Karg's bacnet-stack CLI tools (bacrp/bacwp/bacwi/bacepics).

Pure-Python BACnet MS/TP (as bacpypes provides) cannot reliably meet the trunk's
strict token-passing timing under Python's GIL/scheduler jitter, so - like proven
field deployments - this talks to the trunk through the compiled bacnet-stack
tools instead, driven entirely via environment variables and subprocess calls.
"""

import os
import re
import subprocess
import time

SWEEP_BAUDS = ["38400", "76800", "9600", "19200", "115200"]
_TOTAL_RE = re.compile(r"Total Devices:\s*(\d+)")
_DEVICE_LINE_RE = re.compile(r"^\s*(\d{1,7})(?:\s+([0-9A-Fa-f]{1,2})(?=\s|$))?")
_WRITABLE_TYPES = {
    "analog-output", "analog-value", "binary-output", "binary-value",
    "multi-state-output", "multi-state-value",
}


class MstpError(RuntimeError):
    pass


def _run(cmd, timeout, env):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except FileNotFoundError:
        return 127, "", "tool not found - build bacnet-stack first (scripts/build_bacnet_stack.sh)"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as e:  # noqa: BLE001
        return 1, "", str(e)


def _env(cfg, baud=None, iface=None):
    """Build the bacnet-stack CLI environment.

    `iface`/`baud` default to the configured [mstp] values, but can be
    overridden so gateway/discovery.py can probe an arbitrary /dev/ttyUSB*
    port at an arbitrary baud during auto-detection, without needing a
    dedicated cfg section per port.
    """
    s = cfg["mstp"]
    env = os.environ.copy()
    env.update({
        "BACNET_DATALINK": "mstp",
        "BACNET_IFACE": iface or s["iface"],
        "BACNET_MSTP_IFACE": iface or s["iface"],
        "BACNET_MSTP_BAUD": str(baud or s["baud"]),
        "BACNET_MSTP_MAC": s["our_mac"],
        "BACNET_MAX_MASTER": s["max_master"],
        "BACNET_MAX_INFO_FRAMES": s["max_info_frames"],
        "BACNET_APDU_TIMEOUT": s["apdu_timeout_ms"],
    })
    return env


def _tool(cfg, name):
    from .config import bacnet_bin_dir
    return os.path.join(bacnet_bin_dir(cfg), name)


def _parse_devices_and_macs(text):
    """Parse bacwi output into (devices_list, {device_instance: mac_int}).

    bacwi prints rows like:
        9050    01    0    00    480
    where column 1 is device instance and column 2 is hex MAC (00..7F on MS/TP).
    """
    devices = []
    macs = {}
    for line in (text or "").splitlines():
        low = line.lower()
        if line.lstrip().startswith(";") or ("device" in low and "mac" in low) or "total devices" in low:
            continue
        m = _DEVICE_LINE_RE.match(line)
        if m:
            inst = int(m.group(1))
            if inst not in devices:
                devices.append(inst)
            if m.group(2):
                try:
                    mac = int(m.group(2), 16)
                    if 0 <= mac <= 127:
                        macs[inst] = mac
                except ValueError:
                    pass
    return devices, macs


def _parse_devices(text):
    devices, _ = _parse_devices_and_macs(text)
    return devices


def whois(cfg, timeout=8, iface=None, baud=None):
    """Who-Is sweep at the configured (or overridden) iface/baud.

    Returns discovered device instances and their MS/TP MAC addresses.
    """
    tool = _tool(cfg, "bacwi")
    if not os.path.exists(tool):
        return {"devices": [], "device_macs": {}, "error": "bacnet-stack not built", "ts": time.time()}
    rc, so, se = _run([tool], timeout=timeout, env=_env(cfg, baud, iface))
    if rc not in (0, 124):
        return {"devices": [], "device_macs": {}, "error": se or f"exit {rc}", "ts": time.time()}
    devices, macs = _parse_devices_and_macs(so)
    return {"devices": devices, "device_macs": macs, "ts": time.time()}


def probe_port(cfg, iface, baud, timeout=None):
    """Single-baud MS/TP presence check on an arbitrary port, for discovery.py.

    Returns {"devices": [...], "device_macs": {inst: mac}, "error": str|None}.
    """
    from .config import autodetect_mstp_probe_timeout
    timeout = timeout or autodetect_mstp_probe_timeout(cfg)
    result = whois(cfg, timeout=timeout, iface=iface, baud=baud)
    return {
        "devices": result.get("devices", []),
        "device_macs": result.get("device_macs", {}),
        "error": result.get("error"),
    }


def bus_scan(cfg, iface=None):
    """Sweep common baud rates to find the trunk's actual speed."""
    tool = _tool(cfg, "bacwi")
    if not os.path.exists(tool):
        return {"results": [], "error": "bacnet-stack not built", "ts": time.time()}
    results, best = [], None
    for b in SWEEP_BAUDS:
        rc, so, se = _run([tool], timeout=12, env=_env(cfg, b, iface))
        text = (so or "") + "\n" + (se or "")
        m = _TOTAL_RE.search(text)
        devices, macs = _parse_devices_and_macs(so)
        count = int(m.group(1)) if m else len(devices)
        results.append({"baud": b, "count": count, "devices": devices, "device_macs": macs})
        if count > 0 and best is None:
            best = b
    note = "" if best else "no devices responded at any baud (check A/B polarity, termination, GND)"
    return {"results": results, "best_baud": best, "note": note, "ts": time.time()}


def read_property(cfg, device_instance, obj_type, obj_inst, prop="present-value", timeout=5, iface=None, baud=None):
    tool = _tool(cfg, "bacrp")
    if not os.path.exists(tool):
        raise MstpError("bacnet-stack not built")
    cmd = [tool, str(device_instance), obj_type, str(obj_inst), prop]
    rc, so, se = _run(cmd, timeout=timeout, env=_env(cfg, baud, iface))
    if rc != 0:
        raise MstpError(se or f"bacrp exit {rc}")
    return _coerce(so)


def _bacwp_tag_and_value(obj_type, value):
    """Return (application_tag_str, formatted_value_str) for bacwp.

    value=None sends BACnet NULL (tag 0) to relinquish a priority array slot.
    """
    if value is None or (isinstance(value, str) and value.strip().lower() in ("null", "none", "relinquish", "")):
        return "0", "0"
    ot = str(obj_type or "").lower()
    if ot in ("binary-output", "binary-value", "binary-input", "bo", "bv", "bi"):
        if isinstance(value, str):
            v = "1" if value.strip().lower() in ("1", "true", "active", "on", "yes") else "0"
        else:
            v = "1" if bool(value) else "0"
        return "9", v  # Enumerated (active=1, inactive=0)
    if ot in ("multi-state-output", "multi-state-value", "multi-state-input", "mo", "mv", "mi"):
        return "2", str(int(float(value)))  # Unsigned
    return "4", str(float(value))  # Real


def write_property(cfg, device_instance, obj_type, obj_inst, value, priority=8, prop="present-value", timeout=5, iface=None, baud=None):
    tool = _tool(cfg, "bacwp")
    if not os.path.exists(tool):
        raise MstpError("bacnet-stack not built")
    # bacwp <device-id> <obj-type> <obj-instance> <prop> <priority> <index> <tag> <value>
    tag, formatted = _bacwp_tag_and_value(obj_type, value)
    cmd = [tool, str(device_instance), obj_type, str(obj_inst), prop, str(priority), "-1", tag, formatted]
    rc, so, se = _run(cmd, timeout=timeout, env=_env(cfg, baud, iface))
    if rc != 0:
        raise MstpError(se or f"bacwp exit {rc}")
    return True


def _coerce(text):
    text = (text or "").strip()
    if text.lower() in ("active", "true"):
        return "active"
    if text.lower() in ("inactive", "false"):
        return "inactive"
    m = re.search(r"-?\d+\.?\d*", text)
    if not m:
        return text
    val = m.group(0)
    return float(val) if ("." in val) else int(val)


_EPICS_OID_RE = re.compile(r"Object_Identifier:\s*\(?([A-Za-z-]+),\s*(\d+)\)?", re.IGNORECASE)
_EPICS_NAME_RE = re.compile(r'Object_Name:\s*"?([^"\n]*)"?', re.IGNORECASE)
_EPICS_PV_RE = re.compile(r"Present_Value:\s*([^\n]+)", re.IGNORECASE)
_EPICS_UNITS_RE = re.compile(r"Units:\s*([^\n]+)", re.IGNORECASE)
_EPICS_DESC_RE = re.compile(r'Description:\s*"?([^"\n]*)"?', re.IGNORECASE)


def _clean_epics_val(text):
    val = (text or "").strip().strip('"').strip()
    if val.lower() in ("", "null", "no-units", "unknown-property", "bacnet error"):
        return ""
    if "bacnet error" in val.lower() or "reject" in val.lower():
        return ""
    return val


def parse_epics_objects(text):
    """Parse bacepics output into rich object dicts including value, units, description, writable."""
    objects = []
    # Split either by blank lines or by '{' object blocks so both formats work
    blocks = re.split(r"\n\s*\n|\n(?=\s*\{)", text or "")
    for block in blocks:
        m_oid = _EPICS_OID_RE.search(block)
        if not m_oid:
            continue
        otype = m_oid.group(1).strip().lower()
        oinst = int(m_oid.group(2))
        m_name = _EPICS_NAME_RE.search(block)
        m_pv = _EPICS_PV_RE.search(block)
        m_units = _EPICS_UNITS_RE.search(block)
        m_desc = _EPICS_DESC_RE.search(block)
        pv_raw = _clean_epics_val(m_pv.group(1)) if m_pv else ""
        pv = _coerce(pv_raw) if pv_raw else ""
        units = _clean_epics_val(m_units.group(1)) if m_units else ""
        desc = _clean_epics_val(m_desc.group(1)) if m_desc else ""
        writable = (otype in _WRITABLE_TYPES) or ("priority_array" in block.lower())
        objects.append({
            "type": otype,
            "instance": oinst,
            "name": (m_name.group(1).strip() if m_name else ""),
            "value": pv,
            "units": units,
            "description": desc,
            "writable": writable,
        })
    return objects


def dump_device(cfg, device_instance, timeout=25, iface=None, baud=None):
    """YABE-style object list for a device, via bacepics. Best-effort EPICS parsing."""
    tool = _tool(cfg, "bacepics")
    if not os.path.exists(tool):
        return {"objects": [], "error": "bacnet-stack not built", "ts": time.time()}
    rc, so, se = _run([tool, str(device_instance)], timeout=timeout, env=_env(cfg, baud, iface))
    if rc != 0:
        return {"objects": [], "error": se or f"bacepics exit {rc}", "ts": time.time()}
    objects = parse_epics_objects(so)
    return {"objects": objects, "raw": so if not objects else None, "ts": time.time()}
