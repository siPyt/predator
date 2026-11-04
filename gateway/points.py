"""Point mapping model: defines which field values get exposed as objects on
predator's BACnet/IP virtual device, and where each one's live value comes from.

Stored as a JSON file (list of point dicts). Example shape:

[
  {
    "name": "freezer1_temp",
    "exposed_object": "analog-value:1",
    "writable": false,
    "poll_seconds": 5,
    "source": "mstp",
    "mstp": {"device_instance": 1, "mac": "01", "object": "analog-input:1"}
  },
  {
    "name": "pump1_speed",
    "exposed_object": "analog-value:2",
    "writable": true,
    "poll_seconds": 5,
    "source": "modbus",
    "modbus": {"unit": 1, "register_type": "holding", "address": 100,
               "data_type": "uint16", "scale": 1.0}
  }
]
"""

import json
import os
import re

_OBJ_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*:\d+$")
VALID_SOURCES = ("mstp", "modbus", "static")
VALID_MODBUS_REGISTER_TYPES = ("holding", "input", "coil", "discrete")
VALID_MODBUS_DATA_TYPES = ("int16", "uint16", "int32", "uint32", "float32")


class PointError(ValueError):
    pass


def _require(cond, msg):
    if not cond:
        raise PointError(msg)


def validate_point(p):
    _require(isinstance(p.get("name"), str) and p["name"], "point missing 'name'")
    _require(_OBJ_RE.match(p.get("exposed_object", "")), f"{p['name']}: bad exposed_object")
    _require(p.get("source") in VALID_SOURCES, f"{p['name']}: bad source")
    poll = p.get("poll_seconds", 5)
    _require(isinstance(poll, (int, float)) and poll > 0, f"{p['name']}: bad poll_seconds")

    if p["source"] == "mstp":
        m = p.get("mstp") or {}
        _require(isinstance(m.get("device_instance"), int), f"{p['name']}: mstp.device_instance required")
        _require(_OBJ_RE.match(m.get("object", "")), f"{p['name']}: bad mstp.object")
    elif p["source"] == "modbus":
        m = p.get("modbus") or {}
        _require(isinstance(m.get("unit"), int), f"{p['name']}: modbus.unit required")
        _require(m.get("register_type") in VALID_MODBUS_REGISTER_TYPES, f"{p['name']}: bad modbus.register_type")
        _require(isinstance(m.get("address"), int) and m["address"] >= 0, f"{p['name']}: bad modbus.address")
        _require(m.get("data_type", "uint16") in VALID_MODBUS_DATA_TYPES, f"{p['name']}: bad modbus.data_type")
    return True


def validate_points(points):
    seen_names, seen_objects = set(), set()
    for p in points:
        validate_point(p)
        _require(p["name"] not in seen_names, f"duplicate point name {p['name']}")
        _require(p["exposed_object"] not in seen_objects, f"duplicate exposed_object {p['exposed_object']}")
        seen_names.add(p["name"])
        seen_objects.add(p["exposed_object"])
    return points


def load_points(path):
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        points = json.load(f)
    validate_points(points)
    return points


def save_points(path, points):
    validate_points(points)
    path = os.path.expanduser(path)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(points, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def parse_object(obj_str):
    """'analog-value:1' -> ('analog-value', 1)"""
    obj_type, _, inst = obj_str.partition(":")
    return obj_type, int(inst)
