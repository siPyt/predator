"""Writes the one CSV file the user actually wants to see: everything
predator has found so far, serial and BACnet/IP, auto-detected and curated.

Written atomically (temp file + os.replace) every [csv] write_seconds, to
[csv] path, and mirrored to [csv] desktop_copy so it's double-clickable from
the Pi's own desktop over VNC (gateway/viewer.py is a nicer way to look at it,
but the plain CSV always works even if the viewer isn't running).
"""

import csv
import io
import logging
import os
import shutil
import threading
import time

from . import config as cfgmod
from .points import load_points, parse_object
from .runtime_state import STATE

log = logging.getLogger("predator.csv_export")

FIELDS = [
    "source", "bus_or_network", "address", "object_or_register",
    "data_type", "description", "value", "units", "writable", "last_seen",
]


def _serial_rows():
    rows = []
    for state in STATE.all_port_states():
        if not state.devices:
            rows.append({
                "source": state.protocol, "bus_or_network": state.port,
                "address": "", "object_or_register": "", "data_type": "",
                "description": state.note, "value": "", "units": "",
                "writable": "", "last_seen": _fmt_ts(state.last_scan),
            })
            continue
        last_seen = _fmt_ts(state.last_confirmed or state.last_scan)
        for dev in state.devices:
            objects = (state.device_objects or {}).get(dev) or []
            if not objects:
                # Modbus has no self-describing object list, or MS/TP's
                # bacepics dump failed/returned nothing - still report the
                # device itself so it's not silently missing from the CSV.
                rows.append({
                    "source": state.protocol, "bus_or_network": f"{state.port}@{state.baud}",
                    "address": dev, "object_or_register": "", "data_type": "",
                    "description": state.note, "value": "", "units": "",
                    "writable": "", "last_seen": last_seen,
                })
                continue
            # One row per real BACnet object - this is the device-instance +
            # object-type:instance pair you need to add a point for this into
            # a BMS (e.g. Insight/Apogee) or into predator's own points-map.
            for obj in objects:
                rows.append({
                    "source": state.protocol, "bus_or_network": f"{state.port}@{state.baud}",
                    "address": dev, "object_or_register": f"{obj.get('type')}:{obj.get('instance')}",
                    "data_type": "", "description": obj.get("name", ""), "value": "",
                    "units": "", "writable": "", "last_seen": last_seen,
                })
    return rows


def _bacnet_ip_rows():
    rows = []
    for dev in STATE.all_bacnet_ip_devices():
        rows.append({
            "source": "bacnet-ip", "bus_or_network": dev.address,
            "address": dev.device_instance, "object_or_register": "device",
            "data_type": "", "description": dev.device_name or f"vendor {dev.vendor_id}",
            "value": "", "units": "", "writable": "", "last_seen": _fmt_ts(dev.last_seen),
        })
        for obj in dev.objects:
            rows.append({
                "source": "bacnet-ip", "bus_or_network": dev.address,
                "address": dev.device_instance,
                "object_or_register": f"{obj.get('type')}:{obj.get('instance')}",
                "data_type": "", "description": obj.get("name", ""),
                "value": obj.get("value", ""), "units": "", "writable": "",
                "last_seen": _fmt_ts(dev.last_seen),
            })
    return rows


def _points_map_rows(cfg):
    rows = []
    try:
        points = load_points(cfgmod.points_map_path(cfg))
    except Exception:  # noqa: BLE001
        log.exception("error loading points map for CSV export")
        return rows
    values = STATE.all_point_values()
    for p in points:
        obj_type, obj_inst = parse_object(p["exposed_object"])
        pv = values.get(p["name"], {})
        rows.append({
            "source": f"points-map:{p['source']}", "bus_or_network": p["name"],
            "address": "", "object_or_register": f"{obj_type}:{obj_inst}",
            "data_type": (p.get("modbus") or {}).get("data_type", ""),
            "description": p["name"],
            "value": pv.get("error") or pv.get("value", ""),
            "units": "", "writable": p.get("writable", False),
            "last_seen": _fmt_ts(pv.get("ts")),
        })
    return rows


def _fmt_ts(ts):
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def build_rows(cfg):
    return _serial_rows() + _bacnet_ip_rows() + _points_map_rows(cfg)


def write_csv(cfg):
    rows = build_rows(cfg)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=FIELDS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)

    path = cfgmod.csv_path(cfg)
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(buf.getvalue())
    os.replace(tmp, path)

    desktop_copy = cfgmod.csv_desktop_copy_path(cfg)
    if desktop_copy:
        try:
            os.makedirs(os.path.dirname(desktop_copy) or ".", exist_ok=True)
            shutil.copyfile(path, desktop_copy)
        except Exception:  # noqa: BLE001
            log.exception("error mirroring CSV to desktop copy %s", desktop_copy)
    return path, len(rows)


def run_loop(cfg, stop_event):
    interval = cfgmod.csv_write_seconds(cfg)
    while not stop_event.is_set():
        try:
            path, n = write_csv(cfg)
            log.debug("wrote %d rows to %s", n, path)
        except Exception:  # noqa: BLE001
            log.exception("error writing CSV")
        stop_event.wait(interval)
