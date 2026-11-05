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
_DEVICE_LINE_RE = re.compile(r"^\s*(\d{1,7})\b")


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


def _parse_devices(text):
    devices = []
    for line in (text or "").splitlines():
        low = line.lower()
        if ("device" in low and "mac" in low) or "total devices" in low:
            continue
        m = _DEVICE_LINE_RE.match(line)
        if m:
            inst = int(m.group(1))
            if inst not in devices:
                devices.append(inst)
    return devices


def whois(cfg, timeout=8, iface=None, baud=None):
    """Who-Is sweep at the configured (or overridden) iface/baud.

    Returns discovered device instances. `iface`/`baud` let
    gateway/discovery.py point this at any /dev/ttyUSB* port during
    auto-detection instead of only the one fixed in [mstp].
    """
    tool = _tool(cfg, "bacwi")
    if not os.path.exists(tool):
        return {"devices": [], "error": "bacnet-stack not built", "ts": time.time()}
    rc, so, se = _run([tool], timeout=timeout, env=_env(cfg, baud, iface))
    if rc not in (0, 124):
        return {"devices": [], "error": se or f"exit {rc}", "ts": time.time()}
    return {"devices": _parse_devices(so), "ts": time.time()}


def probe_port(cfg, iface, baud, timeout=None):
    """Single-baud MS/TP presence check on an arbitrary port, for discovery.py.

    Returns {"devices": [...], "error": str|None}. Any non-empty device list
    means something answered a Who-Is as BACnet MS/TP at this iface/baud.
    """
    from .config import autodetect_mstp_probe_timeout
    timeout = timeout or autodetect_mstp_probe_timeout(cfg)
    result = whois(cfg, timeout=timeout, iface=iface, baud=baud)
    return {"devices": result.get("devices", []), "error": result.get("error")}


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
        devices = _parse_devices(so)
        count = int(m.group(1)) if m else len(devices)
        results.append({"baud": b, "count": count, "devices": devices})
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


def write_property(cfg, device_instance, obj_type, obj_inst, value, priority=8, prop="present-value", timeout=5, iface=None, baud=None):
    tool = _tool(cfg, "bacwp")
    if not os.path.exists(tool):
        raise MstpError("bacnet-stack not built")
    # bacwp <device-id> <obj-type> <obj-instance> <prop> <priority> <index> <tag> <value>
    # tag 4 = REAL; callers pass pre-formatted numeric values.
    cmd = [tool, str(device_instance), obj_type, str(obj_inst), prop, str(priority), "-1", "4", str(value)]
    rc, so, se = _run(cmd, timeout=timeout, env=_env(cfg, baud, iface))
    if rc != 0:
        raise MstpError(se or f"bacwp exit {rc}")
    return True


def _coerce(text):
    text = (text or "").strip()
    m = re.search(r"-?\d+\.?\d*", text)
    if not m:
        return text
    val = m.group(0)
    return float(val) if ("." in val) else int(val)


_EPICS_OBJ_RE = re.compile(
    r"Object_Identifier:\s*([A-Za-z-]+),\s*(\d+).*?Object_Name:\s*\"?([^\"\n]*)\"?",
    re.IGNORECASE | re.DOTALL,
)


def dump_device(cfg, device_instance, timeout=20, iface=None, baud=None):
    """YABE-style object list for a device, via bacepics. Best-effort EPICS parsing."""
    tool = _tool(cfg, "bacepics")
    if not os.path.exists(tool):
        return {"objects": [], "error": "bacnet-stack not built", "ts": time.time()}
    rc, so, se = _run([tool, str(device_instance)], timeout=timeout, env=_env(cfg, baud, iface))
    if rc != 0:
        return {"objects": [], "error": se or f"bacepics exit {rc}", "ts": time.time()}
    objects = []
    for block in re.split(r"\n\s*\n", so):
        m = _EPICS_OBJ_RE.search(block)
        if m:
            objects.append({
                "type": m.group(1).strip(),
                "instance": int(m.group(2)),
                "name": m.group(3).strip(),
            })
    return {"objects": objects, "raw": so if not objects else None, "ts": time.time()}
