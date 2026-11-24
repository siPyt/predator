"""Builds an ICC Configuration Studio project (.icsproj) for a Mirius gateway
from the BACnet MS/TP points predator discovers.

Supports:
  - Port A (Index 1): BACnet MS/TP client with one <ServObj> per discovered point
    (auto-populated with real MS/TP MACs parsed from bacwi when available, plus
    optional 32-bit IEEE float per analog point).
  - Optional Port B (Index 0): Siemens FLN Slave (Protocol ID 18) with <FlnObj>
    entries (LDI/LDO/LAI/LAO) matching the exact Database Addresses and 12-char
    RAD50 descriptors from Port A, verified against ConfigurationStudio.exe 3.8.3.0.
  - Persistent DB-address pinning (predator_db_map.json) so newly discovered
    points append at the end instead of shifting existing Port B addresses.
  - Deterministic Excel point map (.xlsx) with live values & units.

Usage:
    python -m gateway.icc_project --xlsx points.xlsx --mac 1 --baud 19200 -o Predator.icsproj
    python -m gateway.icc_project --csv ~/predator_discovered.csv --mac 1 --port-b -o Predator.icsproj
"""

import argparse
import csv
import dataclasses
import io
import json
import logging
import os
import re
import sys
import time
import zipfile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

log = logging.getLogger("predator.icc_project")

# abbreviation -> (BACnet object type id, writable, kind)
OBJECT_TYPES = {
    "AI": (0, False, "analog"), "AO": (1, True, "analog"), "AV": (2, True, "analog"),
    "BI": (3, False, "binary"), "BO": (4, True, "binary"), "BV": (5, True, "binary"),
    "MI": (13, False, "multi"), "MO": (14, True, "multi"), "MV": (19, True, "multi"),
}
_LONG_NAMES = {
    "analog-input": "AI", "analog-output": "AO", "analog-value": "AV",
    "binary-input": "BI", "binary-output": "BO", "binary-value": "BV",
    "multi-state-input": "MI", "multi-state-output": "MO", "multi-state-value": "MV",
}
# bytes one point occupies in the Mirius database (analog/multi-state use 4, as in earlier projects)
_DB_SIZE = {"analog": 4, "multi": 4, "binary": 1}
WRITE_PRIORITY = 16
DEFAULT_BAUD = 19200
DESC_MAX = 12  # Siemens FLN point descriptors (RAD50) are 12 chars; keeping Port A names the same lets both sides match 1:1

_ABBREV = [
    ("TEMPERATURE", "TMP"), ("TEMP", "TMP"), ("PRESSURE", "PRS"), ("REFERENCE", "REF"),
    ("WATER", "WTR"), ("CAPACITY", "CAPAC"), ("SETPOINT", "SP"), ("STATUS", "STAT"),
    ("OPERATION", "OP"), ("ALARM", "ALM"), ("DISCHARGE", "DISCH"), ("EVAPORATOR", "EVAP"),
    ("CONDENSER", "COND"), ("COMPRESSOR", "COMP"), ("SUCTION", "SUCT"), ("SATURATION", "SAT"),
    ("REFRIGERANT", "REFRIG"), ("ENTERING", "ENT"), ("LEAVING", "LVG"), ("OUTDOOR", "OD"),
    ("NETWORK", "NET"), ("ACTIVE", "ACT"), ("ENABLE", "EN"), ("MODE", "MD"), ("PUMP", "PMP"),
]

_UNIT_RAD50 = {
    "degrees-fahrenheit": "DEGF", "degf": "DEGF",
    "degrees-celsius": "DEGC", "degc": "DEGC",
    "pounds-force-per-square-inch": "PSI", "psi": "PSI",
    "us-gallons-per-minute": "GPM", "gpm": "GPM",
    "percent": "PCT", "%": "PCT",
    "percent-relative-humidity": "RH", "%rh": "RH",
    "kilowatts": "KW", "kw": "KW",
    "kilowatt-hours": "KWH", "kwh": "KWH",
    "amperes": "AMPS", "a": "AMPS",
    "volts": "VOLTS", "v": "VOLTS",
    "hertz": "HZ", "hz": "HZ",
    "cubic-feet-per-minute": "CFM", "cfm": "CFM",
    "revolutions-per-minute": "RPM", "rpm": "RPM",
    "hours": "HRS", "minutes": "MIN", "seconds": "SEC",
}


@dataclasses.dataclass
class Point:
    device_instance: int
    mac: int
    obj_type: str          # "AI", "AV", ... (see OBJECT_TYPES)
    instance: int
    name: str
    db_addr: int = None    # None -> allocated by build_project
    use_float: bool = False
    value: str = ""
    units: str = ""


def point_key(p):
    return "%d:%s:%d" % (int(p.device_instance), p.obj_type, int(p.instance))


def normalize_type(value):
    """'analog-input' / 'AI' / 'Analog Input' -> 'AI'; None if not a mappable type."""
    key = str(value or "").strip()
    if key.upper() in OBJECT_TYPES:
        return key.upper()
    return _LONG_NAMES.get(key.lower().replace(" ", "-").replace("_", "-"))


def sanitize_rad50(text, max_len=12, default=""):
    cleaned = re.sub(r"[^A-Za-z0-9]", "", str(text or "")).upper()
    return (cleaned or default)[:max_len]


def format_fln_units(units):
    raw = str(units or "").strip()
    if not raw:
        return ""
    mapped = _UNIT_RAD50.get(raw.lower())
    if mapped:
        return mapped
    return sanitize_rad50(raw, max_len=6)


def suggest_float(p):
    """Hint whether an analog point carries fractional decimals that 16-bit int would truncate."""
    if OBJECT_TYPES.get(p.obj_type, (0, False, ""))[2] != "analog":
        return False
    val = str(p.value or "").strip()
    try:
        f = float(val)
        if f != int(f):
            return True
    except (TypeError, ValueError):
        pass
    return False


def abbreviate(name, used):
    """Upper-case, RAD50-safe, <=12 chars, unique within `used` (which is updated)."""
    text = re.sub(r"[^A-Za-z0-9]", "", str(name or "")).upper() or "POINT"
    for word, short in _ABBREV:
        if len(text) <= DESC_MAX:
            break
        text = text.replace(word, short)
    base = text[:DESC_MAX]
    candidate, n = base, 1
    while candidate in used:
        n += 1
        candidate = base[:DESC_MAX - len(str(n))] + str(n)
    used.add(candidate)
    return candidate


def load_db_map(path):
    """Load pinned {point_key: db_addr} from disk."""
    if not path:
        return {}
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        return {str(k): int(v) for k, v in (raw.get("addresses") or raw).items() if isinstance(v, (int, float))}
    except Exception:  # noqa: BLE001
        log.exception("error loading DB address map %s", path)
        return {}


def save_db_map(path, points, existing=None):
    """Persist pinned {point_key: db_addr} atomically (best-effort)."""
    if not path:
        return
    merged = dict(existing or {})
    for p in points:
        if p.db_addr is not None:
            merged[point_key(p)] = int(p.db_addr)
    path = os.path.expanduser(path)
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"addresses": merged}, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except OSError as e:
        log.warning("could not save DB address map %s: %s", path, e)


_UNIT_BACNET_CODE = {
    "degrees-fahrenheit": 64, "degf": 64,
    "degrees-celsius": 62, "degc": 62,
    "pounds-force-per-square-inch": 56, "psi": 56,
    "us-gallons-per-minute": 89, "gpm": 89,
    "percent": 98, "%": 98,
    "percent-relative-humidity": 29, "%rh": 29,
    "kilowatts": 48, "kw": 48,
    "kilowatt-hours": 19, "kwh": 19,
    "amperes": 3, "a": 3,
    "volts": 5, "v": 5,
    "hertz": 27, "hz": 27,
    "cubic-feet-per-minute": 84, "cfm": 84,
    "revolutions-per-minute": 104, "rpm": 104,
    "inches-of-water": 58, "inwc": 58,
    "hours": 71, "minutes": 72, "seconds": 73,
}


def bacnet_unit_code(units):
    raw = str(units or "").strip().lower()
    if not raw:
        return 95  # no-units
    if raw.isdigit():
        return int(raw)
    return _UNIT_BACNET_CODE.get(raw, 95)


def load_ui_config(path):
    """Load persisted ICC UI overrides (excluded points, float32 points, Port B settings, MACs)."""
    defaults = {
        "include_port_b": None,
        "port_b_protocol": None,
        "fln_baud": None,
        "fln_addr": None,
        "fln_app_num": None,
        "fln_app_desc": "PREDATOR",
        "port_b_baud": None,
        "port_b_mac": None,
        "port_b_device_instance": None,
        "port_b_device_name": "Mirius_B",
        "modbus_baud": None,
        "modbus_parity": None,
        "modbus_addr": None,
        "n2_addr": None,
        "baud_override": None,
        "default_mac": None,
        "device_macs": {},
        "excluded_keys": [],
        "float_keys": [],
    }
    if not path:
        return defaults
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        return defaults
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        out = dict(defaults)
        out.update(raw or {})
        return out
    except Exception:  # noqa: BLE001
        log.exception("error loading ICC UI config %s", path)
        return defaults


def save_ui_config(path, data):
    path = os.path.expanduser(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def allocate_db_addresses(points, pinned_map=None):
    """Fill in missing db_addr sequentially after the highest address already used.

    If `pinned_map` ({'dev:TYPE:inst': addr}) is given, matching points keep their
    pinned DB address and brand-new points append after the highest reserved slot.
    """
    nxt = 0
    if pinned_map:
        for k, addr in pinned_map.items():
            parts = k.split(":")
            ot = parts[1] if len(parts) >= 2 else "AI"
            kind = OBJECT_TYPES.get(ot, (0, False, "analog"))[2]
            nxt = max(nxt, int(addr) + _DB_SIZE[kind])
        for p in points:
            if p.db_addr is None and point_key(p) in pinned_map:
                p.db_addr = int(pinned_map[point_key(p)])

    for p in points:
        if p.db_addr is not None:
            nxt = max(nxt, p.db_addr + _DB_SIZE[OBJECT_TYPES[p.obj_type][2]])
    for p in points:
        if p.db_addr is None:
            p.db_addr = nxt
            nxt += _DB_SIZE[OBJECT_TYPES[p.obj_type][2]]
    return points


_TYPE_ORDER = ["AI", "AV", "AO", "BI", "BV", "BO", "MI", "MV", "MO"]


def sort_points(points):
    """Deterministic order (device, type group, instance) so database addresses
    stay stable from one scan to the next for the same set of points."""
    return sorted(points, key=lambda p: (p.device_instance, _TYPE_ORDER.index(p.obj_type), p.instance))


def descriptions(points, short_names=True):
    """One Port A description per point (12-char unique FLN-style names by default)."""
    used = set()
    return [abbreviate(p.name, used) if short_names else str(p.name)[:32] for p in points]


def _service_object(p, desc, allow_float=True):
    type_id, writable, kind = OBJECT_TYPES[p.obj_type]
    vaddr = (type_id << 22) | p.instance
    is_float = bool(allow_float and p.use_float and kind == "analog")
    lines = [
        ("Desc", desc), ("Inst", p.device_instance), ("Dest", p.mac), ("VAddr", vaddr),
        ("DBAddr", p.db_addr), ("Signed", 1 if is_float else 0), ("Float", 1 if is_float else 0),
    ]
    if kind == "binary":
        lines += [("DSize", 1), ("Bits", 1), ("Bytes", 1)]
    elif is_float:
        lines += [("DSize", 4), ("Bytes", 4), ("Mult", 1)]
    else:
        lines += [("DSize", 2), ("Bytes", 2), ("Mult", 1) if kind == "analog" else ("OB1", 0)]
    lines.append(("RFC", 12))
    if writable:
        lines += [("WFC", 15), ("Pri", WRITE_PRIORITY)]
    body = "".join("              <%s>%s</%s>\r\n" % (tag, escape(str(val)), tag) for tag, val in lines)
    return "            <ServObj>\r\n%s            </ServObj>\r\n" % body


def _bacnet_server_object(p, name, out_instance):
    """Build one BACnet MS/TP Server <BNObj> matching ConfigurationStudio.exe 3.8.3.0."""
    type_id, writable, kind = OBJECT_TYPES[p.obj_type]
    vaddr = (type_id << 22) | int(out_instance)
    is_float = bool(p.use_float and kind == "analog")
    lines = [
        ("Name", str(name)[:32]),
        ("VAddr", vaddr),
        ("DBAddr", p.db_addr),
    ]
    if kind == "analog":
        if is_float:
            lines += [("Signed", 1), ("Float", 1), ("DSize", 4)]
        else:
            lines += [("Signed", 0), ("Float", 0), ("DSize", 2)]
        lines += [("Mult", 1), ("Units", bacnet_unit_code(p.units)), ("CovInc", 0)]
        if writable:
            lines.append(("RDVal", 0))
    elif kind == "multi":
        lines += [("Signed", 0), ("Float", 0), ("DSize", 2), ("NStates", 65535), ("OB1", 0)]
        if writable:
            lines.append(("RDVal", 1))
    else:
        lines += [("Signed", 0), ("Float", 0), ("DSize", 1), ("Mask", 1), ("ATxt", "Active"), ("ITxt", "Inactive")]
        if type_id < 5:
            lines.append(("Pol", 0))
        if writable:
            lines.append(("RDVal", 0))
    body = "".join("              <%s>%s</%s>\r\n" % (tag, escape(str(val)), tag) for tag, val in lines)
    return "            <BNObj>\r\n%s            </BNObj>\r\n" % body


def _build_port_b_bacnet_server(points, baud=38400, mac=10, dev_inst=10000, dev_name="Mirius_B"):
    """Build Mirius Port B (<Index>0</Index>) BACnet MS/TP Server (<ID>3</ID>) XML block."""
    used_names = set()
    used_vaddrs = set()
    objs = []
    for p in points:
        # Ensure unique <=32 char object name on the server
        raw_name = (str(p.name or "").strip() or "%s_%d" % (p.obj_type, p.instance))[:32]
        cand, n = raw_name, 1
        while cand in used_names:
            n += 1
            suffix = "_%d" % n
            cand = raw_name[: 32 - len(suffix)] + suffix
        used_names.add(cand)
        # Ensure unique (obj_type, instance) if multiple Port A devices share an instance
        inst = int(p.instance)
        while (p.obj_type, inst) in used_vaddrs:
            inst += 1
        used_vaddrs.add((p.obj_type, inst))
        objs.append(_bacnet_server_object(p, cand, inst))

    clean_dev_name = (str(dev_name or "Mirius_B").strip() or "Mirius_B")[:16]
    if clean_dev_name == "Mirius":
        clean_dev_name = "Mirius_B"
    clean_inst = max(1, min(int(dev_inst or 10000), 4194302))
    clean_mac = max(0, min(int(mac if mac is not None else 10), 127))
    return (
        "      <Port>\r\n"
        "        <Index>0</Index>\r\n"
        "        <Proto>\r\n"
        "          <ID>3</ID>\r\n"
        "          <Baud>%d</Baud>\r\n"
        "          <Par>0</Par>\r\n"
        "          <TO>1000</TO>\r\n"
        "          <Retry>3</Retry>\r\n"
        "          <MaxMast>127</MaxMast>\r\n"
        "          <MaxIF>1</MaxIF>\r\n"
        "          <Node>\r\n"
        "            <Addr>%d</Addr>\r\n"
        "            <DevName>%s</DevName>\r\n"
        "            <InstNum>%d</InstNum>\r\n"
        "            <VendId>242</VendId>\r\n"
        "            <VendName>ICC, Inc.</VendName>\r\n"
        "            <ModelName>Mirius</ModelName>\r\n"
        "            <RTCEn>0</RTCEn>\r\n"
        "%s"
        "          </Node>\r\n"
        "        </Proto>\r\n"
        "      </Port>\r\n"
    ) % (int(baud or 38400), clean_mac, escape(clean_dev_name), clean_inst, "".join(objs))


def _fln_object(p, desc, pt_num):
    """Build one Siemens FLN Slave <FlnObj> matching ConfigurationStudio.exe 3.8.3.0.

    Protocol object IDs (from ApplicationConfigurationModel.cs):
      1 = LDI (binary RO), 2 = LDO (binary RW), 3 = LAI (analog/multi RO), 4 = LAO (analog/multi RW)
    VAddr = (fln_obj_id << 16) | pt_num
    """
    _, writable, kind = OBJECT_TYPES[p.obj_type]
    if kind == "binary":
        fln_id = 2 if writable else 1
        lines = [
            ("Desc", desc),
            ("VAddr", (fln_id << 16) | int(pt_num)),
            ("DBAddr", p.db_addr),
            ("Signed", 0),
            ("Float", 0),
            ("DSize", 1),
            ("Mask", 1),
            ("ATxt", "ON"),
            ("ITxt", "OFF"),
        ]
    else:
        fln_id = 4 if writable else 3
        lines = [
            ("Desc", desc),
            ("VAddr", (fln_id << 16) | int(pt_num)),
            ("DBAddr", p.db_addr),
            ("Signed", 0),
            ("Float", 0),
            ("DSize", 2),
            ("DefVal", 0),
            ("MaxVal", 32767),
            ("Slope", 1),
            ("Icpt", 0),
            ("Units", format_fln_units(p.units)),
        ]
    body = "".join("              <%s>%s</%s>\r\n" % (tag, escape(str(val)), tag) for tag, val in lines)
    return "            <FlnObj>\r\n%s            </FlnObj>\r\n" % body


def _build_port_b_fln(points, descs, fln_baud=4800, fln_addr=1, fln_app_num=2766, fln_app_desc="PREDATOR"):
    """Build Mirius Port B (<Index>0</Index>) Siemens FLN Slave (<ID>18</ID>) XML block."""
    pt_nums = fln_point_numbers(len(points))
    objs = "".join(
        _fln_object(p, d, pt)
        for p, d, pt in zip(points, descs, pt_nums)
        if pt is not None
    )
    addr = max(0, min(int(fln_addr), 98))
    app_desc = sanitize_rad50(fln_app_desc, max_len=12, default="PREDATOR")
    return (
        "      <Port>\r\n"
        "        <Index>0</Index>\r\n"
        "        <Proto>\r\n"
        "          <ID>18</ID>\r\n"
        "          <Baud>%d</Baud>\r\n"
        "          <Par>0</Par>\r\n"
        "          <TO>0</TO>\r\n"
        "          <SR>0</SR>\r\n"
        "          <Node>\r\n"
        "            <Addr>%02d</Addr>\r\n"
        "            <AppNum>%d</AppNum>\r\n"
        "            <AppDesc>%s</AppDesc>\r\n"
        "            <RevStr>0100</RevStr>\r\n"
        "            <RevNum>1</RevNum>\r\n"
        "%s"
        "          </Node>\r\n"
        "        </Proto>\r\n"
        "      </Port>\r\n"
    ) % (int(fln_baud), addr, int(fln_app_num), escape(app_desc), objs)


def _build_port_b_modbus_slave(baud=9600, parity=0, addr=1):
    """Build Mirius Port B (<Index>0</Index>) Modbus RTU Slave (<ID>2</ID>) with
    <DefMap>1</DefMap> (exposes entire Mirius DB as Holding/Input Registers & Coils)."""
    clean_addr = max(1, min(int(addr or 1), 247))
    clean_par = int(parity or 0)
    if clean_par not in (0, 1, 2, 3):
        clean_par = 0
    return (
        "      <Port>\r\n"
        "        <Index>0</Index>\r\n"
        "        <Proto>\r\n"
        "          <ID>2</ID>\r\n"
        "          <Baud>%d</Baud>\r\n"
        "          <Par>%d</Par>\r\n"
        "          <TO>0</TO>\r\n"
        "          <SR>0</SR>\r\n"
        "          <Node>\r\n"
        "            <Addr>%d</Addr>\r\n"
        "            <DefMap>1</DefMap>\r\n"
        "          </Node>\r\n"
        "        </Proto>\r\n"
        "      </Port>\r\n"
    ) % (int(baud or 9600), clean_par, clean_addr)


def _metasys_n2_object(p, name, n2_type_id, n2_inst):
    """Build one Metasys N2 Slave <N2Obj> matching ConfigurationStudio.exe 3.8.3.0.
    Type IDs: 1=AI, 2=BI, 3=AO, 4=BO, 5=ADF, 6=ADI, 7=BD.
    """
    _, _, kind = OBJECT_TYPES[p.obj_type]
    is_float = bool(p.use_float and kind == "analog")
    lines = [
        ("Name", str(name)[:32]),
        ("VAddr", (int(n2_type_id) << 16) | int(n2_inst)),
        ("DBAddr", p.db_addr),
    ]
    if n2_type_id in (2, 4):  # BI / BO
        lines += [("Signed", 0), ("Float", 0), ("DSize", 1), ("Mask", 1)]
    elif is_float:
        lines += [("Signed", 1), ("Float", 1), ("DSize", 4), ("Mult", 1)]
    else:
        lines += [("Signed", 0), ("Float", 0), ("DSize", 2), ("Mult", 1)]
    body = "".join("              <%s>%s</%s>\r\n" % (tag, escape(str(val)), tag) for tag, val in lines)
    return "            <N2Obj>\r\n%s            </N2Obj>\r\n" % body


def _build_port_b_metasys_n2(points, addr=1):
    """Build Mirius Port B (<Index>0</Index>) Metasys N2 Slave (<ID>12</ID>) at 9600/8N1."""
    counters = {1: 0, 2: 0, 3: 0, 4: 0}
    used_names = set()
    objs = []
    for p in points:
        _, writable, kind = OBJECT_TYPES[p.obj_type]
        n2_type = (4 if writable else 2) if kind == "binary" else (3 if writable else 1)
        if counters[n2_type] >= 256:
            continue
        counters[n2_type] += 1
        raw_name = (str(p.name or "").strip() or "%s_%d" % (p.obj_type, p.instance))[:32]
        cand, n = raw_name, 1
        while cand in used_names:
            n += 1
            suffix = "_%d" % n
            cand = raw_name[: 32 - len(suffix)] + suffix
        used_names.add(cand)
        objs.append(_metasys_n2_object(p, cand, n2_type, counters[n2_type]))
    clean_addr = max(1, min(int(addr or 1), 255))
    return (
        "      <Port>\r\n"
        "        <Index>0</Index>\r\n"
        "        <Proto>\r\n"
        "          <ID>12</ID>\r\n"
        "          <Baud>9600</Baud>\r\n"
        "          <Par>0</Par>\r\n"
        "          <TO>0</TO>\r\n"
        "          <SR>0</SR>\r\n"
        "          <Node>\r\n"
        "            <Addr>%d</Addr>\r\n"
        "%s"
        "          </Node>\r\n"
        "        </Proto>\r\n"
        "      </Port>\r\n"
    ) % (clean_addr, "".join(objs))


def build_project(
    points,
    baud=DEFAULT_BAUD,
    short_names=True,
    include_port_b=False,
    port_b_protocol=None,
    fln_baud=4800,
    fln_addr=1,
    fln_app_num=2766,
    fln_app_desc="PREDATOR",
    port_b_baud=38400,
    port_b_mac=10,
    port_b_device_instance=10000,
    port_b_device_name="Mirius_B",
    modbus_baud=9600,
    modbus_parity=0,
    modbus_addr=1,
    n2_addr=1,
):
    """Return the .icsproj XML text for `points`.

    `port_b_protocol` may be:
      - "none" (default unless include_port_b=True): Port A only
      - "bacnet-mstp": Port B = BACnet MS/TP Server (ID 3)
      - "fln": Port B = Siemens FLN Slave (ID 18, 16-bit unsigned)
      - "modbus-rtu": Port B = Modbus RTU Slave (ID 2, DefMap=1)
      - "metasys-n2": Port B = Metasys N2 Slave (ID 12, 9600/8N1)
    """
    pb = str(port_b_protocol or ("fln" if include_port_b else "none")).strip().lower()
    if pb not in ("none", "fln", "bacnet-mstp", "modbus-rtu", "metasys-n2"):
        pb = "none"

    points = allocate_db_addresses(list(points))
    descs = descriptions(points, short_names)
    allow_float = (pb != "fln")
    objects = "".join(_service_object(p, d, allow_float=allow_float) for p, d in zip(points, descs))
    if pb == "fln":
        port_b = _build_port_b_fln(
            points, descs, fln_baud=fln_baud, fln_addr=fln_addr,
            fln_app_num=fln_app_num, fln_app_desc=fln_app_desc,
        )
    elif pb == "bacnet-mstp":
        port_b = _build_port_b_bacnet_server(
            points, baud=port_b_baud, mac=port_b_mac,
            dev_inst=port_b_device_instance, dev_name=port_b_device_name,
        )
    elif pb == "modbus-rtu":
        port_b = _build_port_b_modbus_slave(baud=modbus_baud, parity=modbus_parity, addr=modbus_addr)
    elif pb == "metasys-n2":
        port_b = _build_port_b_metasys_n2(points, addr=n2_addr)
    else:
        port_b = ""
    return (
        '<?xml version="1.0" encoding="utf-8"?>\r\n'
        "<Proj>\r\n"
        "  <StudioVersion>3.8.3.0</StudioVersion>\r\n"
        "  <Dev>\r\n"
        "    <ID>2102</ID>\r\n"
        "    <Config>\r\n"
        "      <Desc>\r\n      </Desc>\r\n"
        "      <PID>8450</PID>\r\n"
        "      <Endian>1</Endian>\r\n"
        "      <WTrig>0</WTrig>\r\n"
        "      <DefProt>255</DefProt>\r\n"
        "      <ARun>1</ARun>\r\n"
        "      <Lock>0</Lock>\r\n"
        "      <StatCtl>0</StatCtl>\r\n"
        "      <Port>\r\n"
        "        <Index>1</Index>\r\n"
        "        <Proto>\r\n"
        "          <ID>4</ID>\r\n"
        "          <Baud>%d</Baud>\r\n"
        "          <Par>0</Par>\r\n"
        "          <TO>1000</TO>\r\n"
        "          <Retry>3</Retry>\r\n"
        "          <SR>0</SR>\r\n"
        "          <MaxMast>127</MaxMast>\r\n"
        "          <MaxIF>1</MaxIF>\r\n"
        "          <Node>\r\n"
        "            <Addr>0</Addr>\r\n"
        "            <DevName>Mirius</DevName>\r\n"
        "            <InstNum>0</InstNum>\r\n"
        "            <VendId>242</VendId>\r\n"
        "            <VendName>ICC, Inc.</VendName>\r\n"
        "            <ModelName>Mirius</ModelName>\r\n"
        "            <RTCEn>0</RTCEn>\r\n"
        "%s"
        "          </Node>\r\n"
        "        </Proto>\r\n"
        "      </Port>\r\n"
        "%s"
        "    </Config>\r\n"
        "  </Dev>\r\n"
        "</Proj>"
    ) % (baud, objects, port_b)


def encode(xml_text):
    return b"\xef\xbb\xbf" + xml_text.encode("utf-8")


def _write_if_changed(path, data):
    """Atomically write bytes; returns True if the file content changed."""
    path = os.path.expanduser(path)
    try:
        with open(path, "rb") as f:
            if f.read() == data:
                return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return True


def write_project(path, points, baud=DEFAULT_BAUD, short_names=True, **kwargs):
    """Atomically write the project; returns True if the file content changed."""
    return _write_if_changed(path, encode(build_project(points, baud=baud, short_names=short_names, **kwargs)))


# ---- Excel point map -----------------------------------------------------------

FLN_RESERVED = {1, 2, 20, 29, 99}   # Siemens FLN reserved point numbers
FLN_MAX_POINT = 255
XLSX_HEADERS = [
    "Target Device ID", "Object Type", "Object Instance", "Object Name", "Read/Write",
    "Siemens FLN Type (Port B)", "Siemens Point Number", "ICC Database Address",
    "Port A Description (use as FLN Descriptor)", "Present Value", "Units",
]


def fln_point_numbers(count):
    """Suggested FLN point numbers: 3 upward, skipping reserved ones (None once past 255)."""
    numbers, n = [], 3
    while len(numbers) < count:
        if n not in FLN_RESERVED:
            numbers.append(n if n <= FLN_MAX_POINT else None)
        n += 1
    return numbers


def xlsx_bytes(points, short_names=True):
    """The point-mapping workbook as .xlsx bytes (no third-party dependency).
    Deterministic output, so an unchanged point set produces identical bytes."""
    points = allocate_db_addresses(list(points))
    cell = '<c r="%s%d" t="inlineStr"%s><is><t>%s</t></is></c>'
    num = '<c r="%s%d"><v>%s</v></c>'
    rows = ['<row r="1">%s</row>' % "".join(
        cell % (chr(65 + i), 1, ' s="1"', escape(h)) for i, h in enumerate(XLSX_HEADERS))]
    for r, (p, desc, pt) in enumerate(zip(points, descriptions(points, short_names),
                                          fln_point_numbers(len(points))), start=2):
        _, writable, kind = OBJECT_TYPES[p.obj_type]
        fln_type = ("LDO" if writable else "LDI") if kind == "binary" else ("LAO" if writable else "LAI")
        rows.append('<row r="%d">%s</row>' % (r, "".join([
            num % ("A", r, p.device_instance), cell % ("B", r, "", p.obj_type),
            num % ("C", r, p.instance), cell % ("D", r, "", escape(str(p.name))),
            cell % ("E", r, "", "R/W" if writable else "R"),
            cell % ("F", r, "", fln_type),
            num % ("G", r, pt) if pt else "", num % ("H", r, p.db_addr),
            cell % ("I", r, "", desc),
            cell % ("J", r, "", escape(str(p.value if p.value is not None else ""))),
            cell % ("K", r, "", escape(str(p.units or ""))),
        ])))
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    parts = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="%s" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="ICC Point Map" sheetId="1" r:id="rId1"/></sheets></workbook>' % ns,
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
        "xl/styles.xml": '<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="%s"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
            '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill>'
            '<fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border/></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
            '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>' % ns,
        "xl/worksheets/sheet1.xml": '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="%s"><sheetViews><sheetView workbookViewId="0">'
            '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
            '<cols><col min="1" max="3" width="16" customWidth="1"/><col min="4" max="4" width="30" customWidth="1"/>'
            '<col min="5" max="8" width="22" customWidth="1"/><col min="9" max="9" width="38" customWidth="1"/>'
            '<col min="10" max="11" width="16" customWidth="1"/></cols>'
            '<sheetData>%s</sheetData></worksheet>' % (ns, "".join(rows)),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in parts.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, body.encode("utf-8"))
    return buf.getvalue()


def _publish(dest, data, previous_path):
    """Put `data` at `dest`, refreshing it on every change. If `dest` holds something
    other than what was generated last time (e.g. a project the engineer saved Port B
    edits into), it is moved aside with a timestamp instead of being overwritten."""
    dest = os.path.expanduser(dest)
    try:
        with open(dest, "rb") as f:
            current = f.read()
    except OSError:
        current = None
    if current == data:
        return False
    if current is not None:
        try:
            with open(os.path.expanduser(previous_path), "rb") as f:
                previous = f.read()
        except OSError:
            previous = None
        if current != previous:
            base, ext = os.path.splitext(dest)
            aside = "%s (saved %s)%s" % (base, time.strftime("%Y%m%d-%H%M%S"), ext)
            os.replace(dest, aside)
            log.info("kept your edited %s as %s", dest, aside)
    return _write_if_changed(dest, data)


# ---- point sources -------------------------------------------------------------

def _parse_macs(text):
    """'9050=1, 3200=6' -> {9050: 1, 3200: 6}"""
    macs = {}
    for item in str(text or "").split(","):
        if "=" in item:
            dev, mac = item.split("=", 1)
            try:
                macs[int(dev)] = int(mac)
            except ValueError:
                pass
    return macs


def points_from_rows(rows, default_mac=1, device_macs=None):
    """Points from csv_export-style rows for BACnet MS/TP objects.

    Returns (points, baud) - baud is taken from the 'port@baud' column, or None.
    """
    macs = device_macs or {}
    points, baud = [], None
    for row in rows:
        if str(row.get("source", "")).lower() != "mstp":
            continue
        obj = str(row.get("object_or_register", ""))
        if ":" not in obj:
            continue
        type_text, _, inst_text = obj.partition(":")
        obj_type = normalize_type(type_text)
        try:
            device, instance = int(row.get("address")), int(inst_text)
        except (TypeError, ValueError):
            continue
        if not obj_type:
            continue  # device, calendar, schedule, trend-log, ... have no Service Object
        bus = str(row.get("bus_or_network", ""))
        if baud is None and "@" in bus:
            try:
                baud = int(bus.rsplit("@", 1)[1])
            except ValueError:
                pass
        points.append(Point(
            device, macs.get(device, default_mac), obj_type, instance,
            row.get("description") or "%s%d" % (obj_type, instance),
            value=str(row.get("value", "") if row.get("value") is not None else ""),
            units=str(row.get("units", "") or ""),
        ))
    return points, baud


def points_from_csv(path, default_mac=1, device_macs=None):
    with open(os.path.expanduser(path), newline="", encoding="utf-8") as f:
        return points_from_rows(list(csv.DictReader(f)), default_mac, device_macs)


def _xlsx_rows(path):
    """Minimal .xlsx reader (first sheet) so no extra dependency is needed."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            shared = ["".join(t.text or "" for t in si.iter("{%s}t" % ns["m"])) for si in root.findall("m:si", ns)]
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    for row in sheet.iter("{%s}row" % ns["m"]):
        cells = {}
        for c in row.findall("m:c", ns):
            col = re.match(r"[A-Z]+", c.get("r")).group(0)
            if c.get("t") == "inlineStr":
                value = "".join(t.text or "" for t in c.iter("{%s}t" % ns["m"]))
            else:
                v = c.find("m:v", ns)
                value = None if v is None else (shared[int(v.text)] if c.get("t") == "s" else v.text)
            cells[col] = value
        yield cells


def points_from_xlsx(path, default_mac=1, device_macs=None):
    """Points from the point-mapping workbook (Target Device ID / Object Type /
    Object Instance / Object Name / ICC Database Address columns)."""
    macs = device_macs or {}
    rows = _xlsx_rows(path)
    header = {name: col for col, name in next(rows).items() if name}
    need = ["Target Device ID", "Object Type", "Object Instance", "Object Name"]
    missing = [n for n in need if n not in header]
    if missing:
        raise ValueError("workbook is missing column(s): " + ", ".join(missing))
    points = []
    for cells in rows:
        obj_type = normalize_type(cells.get(header["Object Type"]))
        if not obj_type:
            continue
        device = int(float(cells[header["Target Device ID"]]))
        db_col = header.get("ICC Database Address")
        db = cells.get(db_col) if db_col else None
        val_col = header.get("Present Value")
        unit_col = header.get("Units")
        points.append(Point(
            device, macs.get(device, default_mac), obj_type,
            int(float(cells[header["Object Instance"]])), cells.get(header["Object Name"]) or "",
            int(float(db)) if db not in (None, "") else None,
            value=str(cells.get(val_col) or "") if val_col else "",
            units=str(cells.get(unit_col) or "") if unit_col else "",
        ))
    return points


# ---- gateway hook & preview ----------------------------------------------------

def _resolve_options(cfg, discovered_macs=None):
    ui_path = cfg.get("icc", "ui_config_path", fallback="").strip() if cfg.has_section("icc") else ""
    ui = load_ui_config(ui_path)

    # MAC precedence: auto-discovered (bacwi) < config [icc] device_macs < UI overrides
    macs = {}
    for k, v in (discovered_macs or {}).items():
        try:
            macs[int(k)] = int(v)
        except (TypeError, ValueError):
            pass
    macs.update(_parse_macs(cfg.get("icc", "device_macs", fallback="")))
    for k, v in (ui.get("device_macs") or {}).items():
        try:
            macs[int(k)] = int(v)
        except (TypeError, ValueError):
            pass

    default_mac = ui.get("default_mac")
    if default_mac is None:
        default_mac = cfg.getint("icc", "default_mac", fallback=1)

    port_b_protocol = ui.get("port_b_protocol")
    if not port_b_protocol:
        cfg_pb = cfg.get("icc", "port_b_protocol", fallback="").strip().lower()
        if cfg_pb in ("none", "fln", "bacnet-mstp", "modbus-rtu", "metasys-n2"):
            port_b_protocol = cfg_pb
        else:
            inc = ui.get("include_port_b")
            if inc is None:
                inc = cfg.getboolean("icc", "include_port_b", fallback=False)
            port_b_protocol = "fln" if inc else "none"

    fln_baud = ui.get("fln_baud") or cfg.getint("icc", "fln_baud", fallback=4800)
    fln_addr = ui.get("fln_addr") if ui.get("fln_addr") is not None else cfg.getint("icc", "fln_addr", fallback=1)
    fln_app_num = ui.get("fln_app_num") if ui.get("fln_app_num") is not None else cfg.getint("icc", "fln_app_num", fallback=2766)
    fln_app_desc = ui.get("fln_app_desc") or "PREDATOR"
    port_b_baud = ui.get("port_b_baud") or cfg.getint("icc", "port_b_baud", fallback=38400)
    port_b_mac = ui.get("port_b_mac") if ui.get("port_b_mac") is not None else cfg.getint("icc", "port_b_mac", fallback=10)
    port_b_device_instance = ui.get("port_b_device_instance") or cfg.getint("icc", "port_b_device_instance", fallback=10000)
    port_b_device_name = ui.get("port_b_device_name") or cfg.get("icc", "port_b_device_name", fallback="Mirius_B")
    modbus_baud = ui.get("modbus_baud") or cfg.getint("icc", "modbus_baud", fallback=9600)
    modbus_parity = ui.get("modbus_parity") if ui.get("modbus_parity") is not None else cfg.getint("icc", "modbus_parity", fallback=0)
    modbus_addr = ui.get("modbus_addr") if ui.get("modbus_addr") is not None else cfg.getint("icc", "modbus_addr", fallback=1)
    n2_addr = ui.get("n2_addr") if ui.get("n2_addr") is not None else cfg.getint("icc", "n2_addr", fallback=1)
    return {
        "ui": ui,
        "macs": macs,
        "default_mac": int(default_mac),
        "include_port_b": port_b_protocol != "none",
        "port_b_protocol": port_b_protocol,
        "fln_baud": int(fln_baud),
        "fln_addr": int(fln_addr),
        "fln_app_num": int(fln_app_num),
        "fln_app_desc": str(fln_app_desc),
        "port_b_baud": int(port_b_baud),
        "port_b_mac": int(port_b_mac),
        "port_b_device_instance": int(port_b_device_instance),
        "port_b_device_name": str(port_b_device_name),
        "modbus_baud": int(modbus_baud),
        "modbus_parity": int(modbus_parity),
        "modbus_addr": int(modbus_addr),
        "n2_addr": int(n2_addr),
        "baud_override": ui.get("baud_override"),
        "excluded_keys": set(ui.get("excluded_keys") or []),
        "float_keys": set(ui.get("float_keys") or []),
        "db_map_path": cfg.get("icc", "db_map_path", fallback="").strip() if cfg.has_section("icc") else "",
    }


def build_preview(cfg, rows, discovered_macs=None):
    """Return UI-ready preview of all discovered MS/TP points, DB addresses, FLN mapping, and options."""
    opts = _resolve_options(cfg, discovered_macs=discovered_macs)
    all_pts, baud = points_from_rows(rows, default_mac=opts["default_mac"], device_macs=opts["macs"])
    all_pts = sort_points(all_pts)
    pinned = load_db_map(opts["db_map_path"])
    allocate_db_addresses(all_pts, pinned_map=pinned)

    included_pts = [p for p in all_pts if point_key(p) not in opts["excluded_keys"]]
    descs_all = descriptions(all_pts, short_names=True)
    fln_nums = fln_point_numbers(len(included_pts))
    fln_by_key = {point_key(p): pt for p, pt in zip(included_pts, fln_nums)}

    items = []
    for p, desc in zip(all_pts, descs_all):
        k = point_key(p)
        _, writable, kind = OBJECT_TYPES[p.obj_type]
        fln_type = ("LDO" if writable else "LDI") if kind == "binary" else ("LAO" if writable else "LAI")
        items.append({
            "key": k,
            "device_instance": p.device_instance,
            "mac": p.mac,
            "obj_type": p.obj_type,
            "instance": p.instance,
            "name": p.name,
            "value": p.value,
            "units": p.units,
            "writable": writable,
            "kind": kind,
            "db_addr": p.db_addr,
            "pinned": k in pinned,
            "fln_desc": desc,
            "fln_type": fln_type,
            "fln_point": fln_by_key.get(k),
            "included": k not in opts["excluded_keys"],
            "use_float": k in opts["float_keys"],
            "float_suggested": suggest_float(p),
        })

    return {
        "baud": opts["baud_override"] or baud or DEFAULT_BAUD,
        "detected_baud": baud,
        "default_mac": opts["default_mac"],
        "discovered_macs": {str(k): v for k, v in (discovered_macs or {}).items()},
        "device_macs": {str(k): v for k, v in opts["macs"].items()},
        "include_port_b": opts["include_port_b"],
        "port_b_protocol": opts["port_b_protocol"],
        "fln_baud": opts["fln_baud"],
        "fln_addr": opts["fln_addr"],
        "fln_app_num": opts["fln_app_num"],
        "fln_app_desc": opts["fln_app_desc"],
        "port_b_baud": opts["port_b_baud"],
        "port_b_mac": opts["port_b_mac"],
        "port_b_device_instance": opts["port_b_device_instance"],
        "port_b_device_name": opts["port_b_device_name"],
        "modbus_baud": opts["modbus_baud"],
        "modbus_parity": opts["modbus_parity"],
        "modbus_addr": opts["modbus_addr"],
        "n2_addr": opts["n2_addr"],
        "included_count": len(included_pts),
        "total_count": len(all_pts),
        "desktop_icsproj": os.path.expanduser(cfg.get("icc", "desktop_copy", fallback="")),
        "desktop_xlsx": os.path.expanduser(cfg.get("icc", "xlsx_desktop_copy", fallback="")),
        "points": items,
    }


def write_if_configured(cfg, rows, discovered_macs=None):
    """Called from csv_export after each CSV write (or manually from the UI).
    When MS/TP points exist, builds the ICC project and Excel point map and
    refreshes home + desktop copies whenever anything changes.
    """
    if not cfg.has_section("icc") or not cfg.getboolean("icc", "enabled", fallback=False):
        return None
    opts = _resolve_options(cfg, discovered_macs=discovered_macs)
    points, baud = points_from_rows(rows, default_mac=opts["default_mac"], device_macs=opts["macs"])
    if not points:
        return None  # nothing found yet: never replace a good project with an empty one

    points = sort_points(points)
    pinned = load_db_map(opts["db_map_path"])
    allocate_db_addresses(points, pinned_map=pinned)
    if opts["db_map_path"]:
        save_db_map(opts["db_map_path"], points, existing=pinned)

    # Apply UI exclusions and float32 flags
    selected = []
    for p in points:
        k = point_key(p)
        if k in opts["excluded_keys"]:
            continue
        p.use_float = k in opts["float_keys"]
        selected.append(p)
    if not selected:
        return None

    use_baud = int(opts["baud_override"] or baud or DEFAULT_BAUD)
    xml_data = encode(build_project(
        selected,
        baud=use_baud,
        port_b_protocol=opts["port_b_protocol"],
        fln_baud=opts["fln_baud"],
        fln_addr=opts["fln_addr"],
        fln_app_num=opts["fln_app_num"],
        fln_app_desc=opts["fln_app_desc"],
        port_b_baud=opts["port_b_baud"],
        port_b_mac=opts["port_b_mac"],
        port_b_device_instance=opts["port_b_device_instance"],
        port_b_device_name=opts["port_b_device_name"],
        modbus_baud=opts["modbus_baud"],
        modbus_parity=opts["modbus_parity"],
        modbus_addr=opts["modbus_addr"],
        n2_addr=opts["n2_addr"],
    ))
    outputs = (
        (xml_data, "path", "desktop_copy"),
        (xlsx_bytes(selected), "xlsx_path", "xlsx_desktop_copy"),
    )
    changed = False
    for data, home_key, desktop_key in outputs:
        home = cfg.get("icc", home_key)
        desktop = cfg.get("icc", desktop_key, fallback="").strip()
        if desktop:
            changed |= _publish(desktop, data, previous_path=home)  # before `home` is updated
        changed |= _write_if_changed(home, data)
    return changed


def main(argv=None):
    ap = argparse.ArgumentParser(description="Generate an ICC Configuration Studio .icsproj (Mirius, BACnet MS/TP client)")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--xlsx", help="point-mapping workbook")
    src.add_argument("--csv", help="predator_discovered.csv")
    ap.add_argument("--mac", type=int, default=1, help="BACnet MS/TP MAC address of the target device(s)")
    ap.add_argument("--device-mac", action="append", default=[], metavar="INSTANCE=MAC",
                    help="per-device MAC override, repeatable")
    ap.add_argument("--baud", type=int, help="MS/TP baud (default: from the CSV, else %d)" % DEFAULT_BAUD)
    ap.add_argument("--port-b", action="store_true", help="also generate Mirius Port B (Siemens FLN Slave)")
    ap.add_argument("--fln-baud", type=int, default=4800, help="Port B Siemens FLN baud (default: 4800)")
    ap.add_argument("--fln-addr", type=int, default=1, help="Port B Siemens FLN address 0..98 (default: 1)")
    ap.add_argument("--fln-app-num", type=int, default=2766, help="Port B FLN application number (default: 2766)")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--excel", help="also write the Excel point map to this path")
    args = ap.parse_args(argv)

    macs = _parse_macs(",".join(args.device_mac))
    if args.xlsx:
        points, baud = points_from_xlsx(args.xlsx, args.mac, macs), None
    else:
        points, baud = points_from_csv(args.csv, args.mac, macs)
    if not points:
        print("no mappable BACnet points found", file=sys.stderr)
        return 1
    write_project(
        args.output, points, baud=args.baud or baud or DEFAULT_BAUD,
        include_port_b=args.port_b, fln_baud=args.fln_baud,
        fln_addr=args.fln_addr, fln_app_num=args.fln_app_num,
    )
    print("wrote %d points to %s" % (len(points), args.output))
    if args.excel:
        _write_if_changed(args.excel, xlsx_bytes(points))
        print("wrote %s" % args.excel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
