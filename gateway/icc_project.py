"""Builds an ICC Configuration Studio project (.icsproj) for a Mirius gateway
from the BACnet MS/TP points predator discovers.

Only the BACnet MS/TP *client* side (Mirius Port A) is generated: one Service
Object per discovered point, each with a Database Address. The BMS side (e.g.
Siemens FLN Slave on Port B) is left for the engineer to add in the Studio,
pointing at the same Database Addresses, before downloading to the device.

The XML layout mirrors what ConfigurationStudio.exe itself writes
(ApplicationConfigurationModel.cs, "ServObj" entries): VAddr is the BACnet
object-identifier (object type << 22 | instance), analog/multi-state values are
unsigned 16-bit, binary values are 1 bit, and writable types get WFC/Pri.

Usage:
    python -m gateway.icc_project --xlsx points.xlsx --mac 1 --baud 19200 -o Predator.icsproj
    python -m gateway.icc_project --csv ~/predator_discovered.csv --mac 1 -o Predator.icsproj
"""

import argparse
import csv
import dataclasses
import io
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


@dataclasses.dataclass
class Point:
    device_instance: int
    mac: int
    obj_type: str          # "AI", "AV", ... (see OBJECT_TYPES)
    instance: int
    name: str
    db_addr: int = None    # None -> allocated by build_project


def normalize_type(value):
    """'analog-input' / 'AI' / 'Analog Input' -> 'AI'; None if not a mappable type."""
    key = str(value or "").strip()
    if key.upper() in OBJECT_TYPES:
        return key.upper()
    return _LONG_NAMES.get(key.lower().replace(" ", "-").replace("_", "-"))


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


def allocate_db_addresses(points):
    """Fill in missing db_addr sequentially after the highest address already used."""
    nxt = 0
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


def _service_object(p, desc):
    type_id, writable, kind = OBJECT_TYPES[p.obj_type]
    vaddr = (type_id << 22) | p.instance
    lines = [
        ("Desc", desc), ("Inst", p.device_instance), ("Dest", p.mac), ("VAddr", vaddr),
        ("DBAddr", p.db_addr), ("Signed", 0), ("Float", 0),
    ]
    if kind == "binary":
        lines += [("DSize", 1), ("Bits", 1), ("Bytes", 1)]
    else:
        lines += [("DSize", 2), ("Bytes", 2), ("Mult", 1) if kind == "analog" else ("OB1", 0)]
    lines.append(("RFC", 12))
    if writable:
        lines += [("WFC", 15), ("Pri", WRITE_PRIORITY)]
    body = "".join("              <%s>%s</%s>\r\n" % (tag, escape(str(val)), tag) for tag, val in lines)
    return "            <ServObj>\r\n%s            </ServObj>\r\n" % body


def build_project(points, baud=DEFAULT_BAUD, short_names=True):
    """Return the .icsproj XML text for `points` (Port A BACnet MS/TP client only)."""
    points = allocate_db_addresses(list(points))
    objects = "".join(_service_object(p, d) for p, d in zip(points, descriptions(points, short_names)))
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
        "    </Config>\r\n"
        "  </Dev>\r\n"
        "</Proj>"
    ) % (baud, objects)


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


def write_project(path, points, baud=DEFAULT_BAUD, short_names=True):
    """Atomically write the project; returns True if the file content changed."""
    return _write_if_changed(path, encode(build_project(points, baud=baud, short_names=short_names)))


# ---- Excel point map -----------------------------------------------------------

FLN_RESERVED = {1, 2, 20, 29, 99}   # Siemens FLN reserved point numbers
FLN_MAX_POINT = 255
XLSX_HEADERS = [
    "Target Device ID", "Object Type", "Object Instance", "Object Name", "Read/Write",
    "Siemens FLN Type (Port B)", "Siemens Point Number", "ICC Database Address",
    "Port A Description (use as FLN Descriptor)",
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
        rows.append('<row r="%d">%s</row>' % (r, "".join([
            num % ("A", r, p.device_instance), cell % ("B", r, "", p.obj_type),
            num % ("C", r, p.instance), cell % ("D", r, "", escape(str(p.name))),
            cell % ("E", r, "", "R/W" if writable else "R"),
            cell % ("F", r, "", "LDI" if kind == "binary" else "LAI"),
            num % ("G", r, pt) if pt else "", num % ("H", r, p.db_addr),
            cell % ("I", r, "", desc),
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
            '<col min="5" max="8" width="22" customWidth="1"/><col min="9" max="9" width="38" customWidth="1"/></cols>'
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
        points.append(Point(device, macs.get(device, default_mac), obj_type, instance,
                            row.get("description") or "%s%d" % (obj_type, instance)))
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
        points.append(Point(device, macs.get(device, default_mac), obj_type,
                            int(float(cells[header["Object Instance"]])), cells.get(header["Object Name"]) or "",
                            int(float(db)) if db not in (None, "") else None))
    return points


# ---- gateway hook --------------------------------------------------------------

def write_if_configured(cfg, rows):
    """Called from csv_export after each CSV write. When MS/TP points exist, builds
    the ICC project and the Excel point map and refreshes them (home copy + desktop
    copy) whenever the points change. Returns True if anything was written.
    """
    if not cfg.has_section("icc") or not cfg.getboolean("icc", "enabled", fallback=False):
        return None
    points, baud = points_from_rows(
        rows, default_mac=cfg.getint("icc", "default_mac", fallback=1),
        device_macs=_parse_macs(cfg.get("icc", "device_macs", fallback="")))
    if not points:
        return None  # nothing found yet: never replace a good project with an empty one
    points = allocate_db_addresses(sort_points(points))
    outputs = (
        (encode(build_project(points, baud=baud or DEFAULT_BAUD)), "path", "desktop_copy"),
        (xlsx_bytes(points), "xlsx_path", "xlsx_desktop_copy"),
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
    write_project(args.output, points, baud=args.baud or baud or DEFAULT_BAUD)
    print("wrote %d points to %s" % (len(points), args.output))
    if args.excel:
        _write_if_changed(args.excel, xlsx_bytes(points))
        print("wrote %s" % args.excel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
