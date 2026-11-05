"""PROFINET discovery with DCP Identify All (raw Ethernet 0x8892; needs Linux and root/CAP_NET_RAW)."""
import socket
import struct
import time

from ..context import Context
from ..records import Record

ETHERTYPE = 0x8892
DCP_MULTICAST = bytes.fromhex("010ecf000000")
ROLES = {0x01: "IO-Device", 0x02: "IO-Controller", 0x04: "IO-Multidevice", 0x08: "PN-Supervisor"}


def build_identify_all(src_mac, xid=0x50524544):
    dcp = struct.pack(">BBIHH", 5, 0, xid, 1, 4) + bytes([0xFF, 0xFF, 0, 0])
    frame = DCP_MULTICAST + src_mac + struct.pack(">H", ETHERTYPE) + b"\xfe\xfe" + dcp
    return frame.ljust(60, b"\0")


def parse_identify_response(frame):
    """Station info dict from a DCP Identify response frame, or None."""
    if len(frame) < 28 or struct.unpack(">H", frame[12:14])[0] != ETHERTYPE:
        return None
    if frame[14:16] != b"\xfe\xff" or frame[16] != 5 or frame[17] != 1:
        return None
    total = struct.unpack(">H", frame[24:26])[0]
    body, info, i = frame[26 : 26 + total], {"mac": frame[6:12].hex(":")}, 0
    while i + 4 <= len(body):
        opt, sub, ln = body[i], body[i + 1], struct.unpack(">H", body[i + 2 : i + 4])[0]
        val = body[i + 4 : i + 4 + ln]
        if (opt, sub) == (2, 1):
            info["type_of_station"] = val[2:].decode("ascii", "replace")
        elif (opt, sub) == (2, 2):
            info["name_of_station"] = val[2:].decode("ascii", "replace")
        elif (opt, sub) == (2, 3) and len(val) >= 6:
            info["vendor_id"], info["device_id"] = "0x%04x" % struct.unpack(">H", val[2:4])[0], "0x%04x" % struct.unpack(">H", val[4:6])[0]
        elif (opt, sub) == (2, 4) and len(val) >= 3:
            info["role"] = ",".join(n for bit, n in ROLES.items() if val[2] & bit)
        elif (opt, sub) == (1, 2) and len(val) >= 14:
            info["ip"], info["netmask"], info["gateway"] = (socket.inet_ntoa(val[2 + 4 * k : 6 + 4 * k]) for k in range(3))
        i += 4 + ln + (ln & 1)
    return info


def _interfaces(ctx):
    if ctx.iface:
        return [ctx.iface]
    import os

    return [n for n in sorted(os.listdir("/sys/class/net")) if n != "lo" and n.startswith(("eth", "en"))]


def scan(ctx: Context):
    if not hasattr(socket, "AF_PACKET"):
        ctx.log("profinet: raw Ethernet needs Linux (AF_PACKET); skipped")
        return
    for iface in _interfaces(ctx):
        try:
            sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETHERTYPE))
            sock.bind((iface, 0))
            with open("/sys/class/net/%s/address" % iface) as fh:
                mac = bytes.fromhex(fh.read().strip().replace(":", ""))
        except (PermissionError, OSError) as exc:
            ctx.log("profinet %s: %s (run with sudo)" % (iface, exc))
            continue
        seen = set()
        try:
            sock.send(build_identify_all(mac))
            end = time.monotonic() + max(ctx.timeout, 4)
            while time.monotonic() < end:
                sock.settimeout(max(0.05, end - time.monotonic()))
                try:
                    info = parse_identify_response(sock.recv(1600))
                except socket.timeout:
                    break
                if info and info["mac"] not in seen:
                    seen.add(info["mac"])
                    ctx.emit(Record("PROFINET", iface, info.get("name_of_station", info["mac"]), "device",
                                    info.get("type_of_station", ""), details=info))
        finally:
            sock.close()
        ctx.log("profinet %s: %d device(s)" % (iface, len(seen)))
