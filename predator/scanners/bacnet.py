"""BACnet/IP discovery (Who-Is, then names/values over ReadProperty) and passive BACnet MS/TP capture."""
import socket
import struct
import time

from ..context import Context, SerialSpec
from ..records import Record
from ..serialutil import open_serial

PORT = 47808
OBJECT_TYPES = {
    0: "analog-input", 1: "analog-output", 2: "analog-value", 3: "binary-input", 4: "binary-output",
    5: "binary-value", 8: "device", 13: "multi-state-input", 14: "multi-state-output",
    17: "schedule", 19: "multi-state-value", 20: "trend-log",
}
VALUE_TYPES = {0, 1, 2, 3, 4, 5, 13, 14, 19}
PROP_NAME, PROP_VALUE, PROP_UNITS, PROP_LIST, PROP_DESC = 77, 85, 117, 76, 28
PROP_VENDOR, PROP_MODEL, PROP_FW = 121, 70, 44
UNITS = {3: "A", 5: "V", 19: "kWh", 29: "%RH", 47: "W", 48: "kW", 62: "degC", 64: "degF", 95: "", 98: "%"}
VENDORS = {7: "Johnson Controls", 5: "Honeywell", 8: "Siemens Industry", 10: "Schneider Electric",
           24: "Delta Controls", 35: "Trane", 36: "Carrier", 142: "Siemens Building Technologies"}
WHO_IS = bytes.fromhex("810b000c0120ffff00ff1008")


# ---- decoding ---------------------------------------------------------------

def decode_tag(data, i):
    """Returns (tag, is_context, kind, length_or_value, next_index); kind: data/open/close."""
    b = data[i]
    tag, ctx, lvt = b >> 4, bool(b & 8), b & 7
    i += 1
    if tag == 15:
        tag = data[i]
        i += 1
    if ctx and lvt == 6:
        return tag, True, "open", 0, i
    if ctx and lvt == 7:
        return tag, True, "close", 0, i
    if not ctx and tag == 1:
        return tag, False, "data", lvt, i
    ln = lvt
    if lvt == 5:
        ln = data[i]
        i += 1
        if ln == 254:
            ln = struct.unpack(">H", data[i : i + 2])[0]
            i += 2
        elif ln == 255:
            ln = struct.unpack(">I", data[i : i + 4])[0]
            i += 4
    return tag, ctx, "data", ln, i


def decode_object_id(raw):
    n = struct.unpack(">I", raw)[0]
    return n >> 22, n & 0x3FFFFF


def decode_app(tag, raw, ln):
    if tag == 0:
        return None
    if tag == 1:
        return bool(ln)
    if tag == 2 or tag == 9:
        return int.from_bytes(raw, "big")
    if tag == 3:
        return int.from_bytes(raw, "big", signed=True)
    if tag == 4:
        return round(struct.unpack(">f", raw)[0], 4)
    if tag == 5:
        return round(struct.unpack(">d", raw)[0], 6)
    if tag == 6:
        return raw.hex()
    if tag == 7:
        enc, txt = raw[0], raw[1:]
        return txt.decode("utf-16-be" if enc == 4 else "latin-1" if enc == 5 else "utf-8", "replace")
    if tag == 8:
        return "bits:" + raw[1:].hex()
    if tag == 12:
        t, inst = decode_object_id(raw)
        return "%s:%d" % (OBJECT_TYPES.get(t, t), inst)
    return raw.hex()


def parse_values(data, i):
    """Decode application-tagged values starting at i until a closing tag; returns (values, index)."""
    vals = []
    while i < len(data):
        tag, ctx, kind, ln, j = decode_tag(data, i)
        if kind == "close":
            return vals, j
        if kind == "open":
            inner, i = parse_values(data, j)
            vals.append(inner)
            continue
        raw = data[j : j + ln] if not (not ctx and tag == 1) else b""
        vals.append(decode_app(tag, raw, ln) if not ctx else raw.hex())
        i = j + (0 if (not ctx and tag == 1) else ln)
    return vals, i


def parse_npdu(data):
    """Returns (info dict, offset of APDU) or None."""
    if len(data) < 2 or data[0] != 1:
        return None
    ctl, i, info = data[1], 2, {}
    if ctl & 0x20:
        dnet, dlen = struct.unpack(">HB", data[i : i + 3])
        i += 3 + dlen
        info["dnet"] = dnet
    if ctl & 0x08:
        snet, slen = struct.unpack(">HB", data[i : i + 3])
        info["snet"] = snet
        info["sadr"] = data[i + 3 : i + 3 + slen].hex()
        i += 3 + slen
    if ctl & 0x20:
        i += 1
    if ctl & 0x80:
        return None
    return info, i


def parse_iam(apdu):
    if len(apdu) < 4 or apdu[0] != 0x10 or apdu[1] != 0x00:
        return None
    try:
        tag, _c, _k, ln, i = decode_tag(apdu, 2)
        otype, inst = decode_object_id(apdu[i : i + 4])
        i += 4
        out = {"object_type": otype, "instance": inst}
        for key in ("max_apdu", "segmentation", "vendor_id"):
            tag, _c, _k, ln, i = decode_tag(apdu, i)
            out[key] = int.from_bytes(apdu[i : i + ln], "big")
            i += ln
        return out
    except (IndexError, struct.error):
        return None


def parse_read_ack(apdu, invoke):
    """Values of a ReadProperty ComplexACK, or None for error/reject/abort/other."""
    if len(apdu) < 4 or apdu[0] != 0x30 or apdu[1] != invoke or apdu[2] != 0x0C:
        return None
    try:
        i = 3
        while True:
            tag, ctx, kind, ln, j = decode_tag(apdu, i)
            if kind == "open" and tag == 3:
                vals, _ = parse_values(apdu, j)
                return vals
            i = j + ln
    except (IndexError, struct.error):
        return None


PROPS = {85: "present-value", 77: "object-name", 28: "description", 111: "status-flags", 36: "event-state",
         117: "units", 81: "out-of-service", 103: "reliability", 70: "model-name", 44: "firmware-revision"}


def parse_tree(data, i=0):
    """Nested tag items: ('c', tag, raw) context, ('a', tag, value) application, ('o', tag, [items]) opened."""
    items = []
    while i < len(data):
        tag, ctx, kind, ln, j = decode_tag(data, i)
        if kind == "close":
            return items, j
        if kind == "open":
            inner, i = parse_tree(data, j)
            items.append(("o", tag, inner))
            continue
        is_bool = not ctx and tag == 1
        raw = b"" if is_bool else data[j : j + ln]
        items.append(("c", tag, raw) if ctx else ("a", tag, decode_app(tag, raw, ln)))
        i = j + (0 if is_bool else ln)
    return items, i


def _values(items):
    vals = [v for k, _t, v in items if k == "a"]
    return vals[0] if len(vals) == 1 else (vals or None)


def _prop(code):
    return PROPS.get(code, "property-%d" % code)


def decode_apdu_points(apdu):
    """[(object type, instance, property name, value)] carried by a ReadProperty/RPM ACK or COV notification."""
    if not apdu:
        return []
    kind = apdu[0] >> 4
    try:
        if kind == 3 and not apdu[0] & 0x08 and len(apdu) > 3:
            svc, body = apdu[2], apdu[3:]
        elif kind == 1 and len(apdu) > 1:
            svc, body = apdu[1], apdu[2:]
        elif kind == 0 and not apdu[0] & 0x08 and len(apdu) > 4:
            svc, body = apdu[3], apdu[4:]
        else:
            return []
        if (kind, svc) not in ((3, 12), (3, 14), (1, 2), (0, 1)):
            return []
        items, _ = parse_tree(body)
        out = []
        if (kind, svc) == (3, 12):
            oid = next((v for k, t, v in items if k == "c" and t == 0), None)
            prop = next((v for k, t, v in items if k == "c" and t == 1), None)
            vals = next((v for k, t, v in items if k == "o" and t == 3), None)
            if oid and prop is not None and vals is not None and len(oid) == 4:
                otype, inst = decode_object_id(oid)
                out.append((OBJECT_TYPES.get(otype, "type-%d" % otype), inst, _prop(int.from_bytes(prop, "big")), _values(vals)))
        elif (kind, svc) == (3, 14):
            cur = None
            for k, t, v in items:
                if k == "c" and t == 0 and len(v) == 4:
                    cur = decode_object_id(v)
                elif k == "o" and t == 1 and cur:
                    prop = None
                    for k2, t2, v2 in v:
                        if k2 == "c" and t2 == 2:
                            prop = int.from_bytes(v2, "big")
                        elif k2 == "o" and t2 == 4 and prop is not None:
                            out.append((OBJECT_TYPES.get(cur[0], "type-%d" % cur[0]), cur[1], _prop(prop), _values(v2)))
        else:
            mon = next((v for k, t, v in items if k == "c" and t == 2 and len(v) == 4), None)
            lst = next((v for k, t, v in items if k == "o" and t == 4), [])
            if mon:
                otype, inst = decode_object_id(mon)
                prop = None
                for k, t, v in lst:
                    if k == "c" and t == 0:
                        prop = int.from_bytes(v, "big")
                    elif k == "o" and t == 2 and prop is not None:
                        out.append((OBJECT_TYPES.get(otype, "type-%d" % otype), inst, _prop(prop), _values(v)))
        return out
    except (IndexError, struct.error, ValueError):
        return []


# ---- encoding ---------------------------------------------------------------

def encode_read_property(invoke, otype, inst, prop, index=None, dest=None):
    ctl = 0x04 | (0x20 if dest else 0)
    npdu = bytes([1, ctl])
    if dest:
        dnet, dadr = dest
        npdu += struct.pack(">HB", dnet, len(dadr)) + dadr + b"\xff"
    apdu = bytes([0x00, 0x05, invoke, 0x0C, 0x0C]) + struct.pack(">I", (otype << 22) | inst)
    apdu += bytes([0x19, prop])
    if index is not None:
        apdu += bytes([0x29, index]) if index < 256 else b"\x2a" + struct.pack(">H", index)
    body = npdu + apdu
    return b"\x81\x0a" + struct.pack(">H", len(body) + 4) + body


class Client:
    def __init__(self, timeout):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            self.sock.bind(("", PORT))
        except OSError:
            self.sock.bind(("", 0))
        self.timeout = timeout
        self.invoke = 0

    def discover(self, targets, seconds):
        for t in targets:
            try:
                self.sock.sendto(WHO_IS, (t, PORT))
            except OSError:
                pass
        devices = {}
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.sock.settimeout(max(0.05, end - time.monotonic()))
            try:
                data, (ip, _p) = self.sock.recvfrom(1500)
            except (socket.timeout, OSError):
                continue
            if len(data) < 6 or data[0] != 0x81 or data[1] not in (0x0A, 0x0B, 0x04):
                continue
            off = 4 if data[1] != 0x04 else 10
            parsed = parse_npdu(data[off:])
            if not parsed:
                continue
            info, a = parsed
            iam = parse_iam(data[off + a :])
            if iam and iam["object_type"] == 8:
                dest = (info["snet"], bytes.fromhex(info["sadr"])) if "snet" in info else None
                devices[(ip, iam["instance"], info.get("snet"), info.get("sadr"))] = (iam, dest)
        return devices

    def read(self, ip, otype, inst, prop, index=None, dest=None):
        self.invoke = (self.invoke + 1) & 0xFF
        self.sock.sendto(encode_read_property(self.invoke, otype, inst, prop, index, dest), (ip, PORT))
        end = time.monotonic() + self.timeout
        while time.monotonic() < end:
            self.sock.settimeout(max(0.05, end - time.monotonic()))
            try:
                data, (src, _p) = self.sock.recvfrom(1500)
            except (socket.timeout, OSError):
                return None
            if src != ip or len(data) < 8 or data[0] != 0x81 or data[1] != 0x0A:
                continue
            parsed = parse_npdu(data[4:])
            if not parsed:
                continue
            apdu = data[4 + parsed[1] :]
            if len(apdu) >= 2 and apdu[1] == self.invoke and apdu[0] >> 4 in (5, 6, 7):
                return None
            vals = parse_read_ack(apdu, self.invoke)
            if vals is not None:
                return vals
        return None

    def close(self):
        self.sock.close()


def _first(vals):
    return vals[0] if vals else None


def read_device(client, ip, inst, dest, ctx):
    src = "%s%s" % (ip, "" if not dest else " via net %d mac %s" % (dest[0], dest[1].hex()))
    meta = {}
    for key, prop in (("name", PROP_NAME), ("vendor", PROP_VENDOR), ("model", PROP_MODEL), ("firmware", PROP_FW)):
        meta[key] = _first(client.read(ip, 8, inst, prop, dest=dest) or [])
    ctx.emit(Record("BACnet", src, str(inst), "device", meta.get("name") or "", details=meta))
    count = _first(client.read(ip, 8, inst, PROP_LIST, 0, dest) or [])
    if not isinstance(count, int):
        return
    for idx in range(1, min(count, ctx.max_points) + 1):
        oid = _first(client.read(ip, 8, inst, PROP_LIST, idx, dest) or [])
        if not isinstance(oid, str) or ":" not in oid:
            continue
        tname, num = oid.rsplit(":", 1)
        otype = next((k for k, v in OBJECT_TYPES.items() if v == tname), None)
        if otype is None and tname.isdigit():
            otype = int(tname)
        if otype is None:
            continue
        num = int(num)
        name = _first(client.read(ip, otype, num, PROP_NAME, dest=dest) or [])
        value = units = ""
        if otype in VALUE_TYPES:
            value = _first(client.read(ip, otype, num, PROP_VALUE, dest=dest) or [])
            if otype in (0, 1, 2):
                code = _first(client.read(ip, otype, num, PROP_UNITS, dest=dest) or [])
                units = UNITS.get(code, "bacnet-units:%s" % code if code is not None else "")
        ctx.emit(Record("BACnet", src, str(inst), "%s:%d" % (tname, num), value, units, {"name": name}))


def scan(ctx: Context):
    client = Client(ctx.timeout)
    try:
        targets = ["255.255.255.255"] + ctx.broadcasts() + ctx.hosts()
        devices = client.discover(targets, ctx.timeout + 2)
        ctx.log("bacnet: %d device(s)" % len(devices))
        for (ip, inst, snet, _sadr), (iam, dest) in sorted(devices.items(), key=lambda kv: str(kv[0])):
            vendor = VENDORS.get(iam["vendor_id"], "vendor-id %d" % iam["vendor_id"])
            ctx.log("  %s device %d (%s)" % (ip, inst, vendor))
            read_device(client, ip, inst, dest, ctx)
    finally:
        client.close()


# ---- MS/TP passive capture --------------------------------------------------

def mstp_header_crc(data):
    crc = 0xFF
    for b in data:
        crc ^= b
        crc = crc ^ (crc << 1) ^ (crc << 2) ^ (crc << 3) ^ (crc << 4) ^ (crc << 5) ^ (crc << 6) ^ (crc << 7)
        crc = (crc & 0xFE) ^ ((crc >> 8) & 1)
    return (~crc) & 0xFF


def mstp_data_crc(data):
    crc = 0xFFFF
    for b in data:
        low = (crc & 0xFF) ^ b
        crc = ((crc >> 8) ^ (low << 8) ^ (low << 3) ^ (low << 12) ^ (low >> 4) ^ (low & 0x0F) ^ ((low & 0x0F) << 7)) & 0xFFFF
    return (~crc) & 0xFFFF


FRAME_TYPES = {0: "token", 1: "poll-for-master", 2: "reply-to-poll", 3: "test-request", 4: "test-response",
               5: "data-expecting-reply", 6: "data-not-expecting-reply", 7: "reply-postponed"}


def parse_mstp(buf):
    """Yields (frame_type, dest, src, data) for each valid frame; returns remaining bytes via list."""
    frames, i = [], 0
    while i + 8 <= len(buf):
        if buf[i] != 0x55 or buf[i + 1] != 0xFF:
            i += 1
            continue
        hdr = buf[i + 2 : i + 7]
        if mstp_header_crc(hdr) != buf[i + 7]:
            i += 1
            continue
        ftype, dest, src, ln = hdr[0], hdr[1], hdr[2], (hdr[3] << 8) | hdr[4]
        if ln == 0:
            frames.append((ftype, dest, src, b""))
            i += 8
            continue
        end = i + 8 + ln + 2
        if end > len(buf):
            break
        data = buf[i + 8 : i + 8 + ln]
        if mstp_data_crc(data) != buf[end - 2] | (buf[end - 1] << 8):
            i += 1
            continue
        frames.append((ftype, dest, src, data))
        i = end
    return frames, buf[i:]


def scan_serial(ctx: Context, spec: SerialSpec):
    for baud in spec.bauds or [38400, 9600, 19200, 76800]:
        ctx.log("bacnet-mstp %s %d baud (passive, %ds)" % (spec.port, baud, ctx.sniff_seconds))
        ser = open_serial(spec.port, baud, "N", 0.1)
        masters, devices, buf, last = {}, set(), b"", {}
        deadline = time.monotonic() + ctx.sniff_seconds
        try:
            while time.monotonic() < deadline:
                buf += ser.read(512)
                frames, buf = parse_mstp(buf)
                for ftype, dest, src, data in frames:
                    if ftype in (0, 1, 2, 5, 6) and src not in masters:
                        masters[src] = FRAME_TYPES[ftype]
                        ctx.emit(Record("BACnet MS/TP", spec.port, str(src), "station", FRAME_TYPES[ftype],
                                        details={"baud": baud}))
                    if ftype in (5, 6) and data:
                        parsed = parse_npdu(data)
                        iam = parse_iam(data[parsed[1]:]) if parsed else None
                        if iam and iam["object_type"] == 8 and (src, iam["instance"]) not in devices:
                            devices.add((src, iam["instance"]))
                            ctx.emit(Record("BACnet MS/TP", spec.port, str(src), "device", iam["instance"],
                                            details={"vendor_id": iam["vendor_id"], "baud": baud}))
                        for otype, inst, prop, value in decode_apdu_points(data[parsed[1]:] if parsed else b""):
                            key = (src, otype, inst, prop)
                            if last.get(key, object()) == value:
                                continue
                            last[key] = value
                            point = "%s:%d" % (otype, inst) + ("" if prop == "present-value" else "/" + prop)
                            ctx.emit(Record("BACnet MS/TP", spec.port, str(src), point, value,
                                            details={"baud": baud, "via": "passive capture"}))
        finally:
            ser.close()
        if masters:
            return
