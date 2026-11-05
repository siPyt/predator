"""EtherNet/IP ListIdentity, KNXnet/IP search, OPC UA hello, and Siemens S7 module identification."""
import socket
import struct
import time

from ..context import Context
from ..records import Record

# ---- EtherNet/IP ------------------------------------------------------------
ENIP_PORT = 44818
LIST_IDENTITY = struct.pack("<HHIIQI", 0x63, 0, 0, 0, 0, 0)


def parse_list_identity(data):
    if len(data) < 26 or struct.unpack("<H", data[:2])[0] != 0x63:
        return None
    count = struct.unpack("<H", data[24:26])[0]
    if count < 1 or len(data) < 30 or struct.unpack("<H", data[26:28])[0] != 0x0C:
        return None
    b = data[30:]
    try:
        vendor, dtype, product, rmaj, rmin, status, serial = struct.unpack("<HHHBBHI", b[18:32])
        name = b[33 : 33 + b[32]].decode("ascii", "replace")
    except (struct.error, IndexError):
        return None
    return {"vendor_id": vendor, "device_type": dtype, "product_code": product,
            "revision": "%d.%d" % (rmaj, rmin), "serial": "0x%08x" % serial, "product_name": name}


def scan_enip(ctx: Context):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    seen = set()
    try:
        for t in ["255.255.255.255"] + ctx.broadcasts() + ctx.hosts():
            try:
                sock.sendto(LIST_IDENTITY, (t, ENIP_PORT))
            except OSError:
                pass
        end = time.monotonic() + ctx.timeout
        while time.monotonic() < end:
            sock.settimeout(max(0.05, end - time.monotonic()))
            try:
                data, (ip, _p) = sock.recvfrom(1500)
            except (socket.timeout, OSError):
                break
            info = parse_list_identity(data)
            if info and ip not in seen:
                seen.add(ip)
                ctx.emit(Record("EtherNet/IP", ip, info["serial"], "device", info["product_name"], details=info))
    finally:
        sock.close()


# ---- KNXnet/IP --------------------------------------------------------------
KNX_GROUP, KNX_PORT = "224.0.23.12", 3671


def parse_knx_search_response(data):
    if len(data) < 14 or data[:4] != b"\x06\x10\x02\x02":
        return None
    i = 6 + 8
    while i + 2 <= len(data):
        ln, dtype = data[i], data[i + 1]
        if ln < 2:
            break
        if dtype == 1 and ln >= 54:
            d = data[i : i + ln]
            a = struct.unpack(">H", d[4:6])[0]
            return {"knx_address": "%d.%d.%d" % (a >> 12, (a >> 8) & 15, a & 255),
                    "mac": d[18:24].hex(":"), "friendly_name": d[24:54].split(b"\0")[0].decode("latin-1")}
        i += ln
    return None


def scan_knx(ctx: Context):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    try:
        sock.bind(("", 0))
        req = b"\x06\x10\x02\x01\x00\x0e" + b"\x08\x01\x00\x00\x00\x00" + struct.pack(">H", sock.getsockname()[1])
        sock.sendto(req, (KNX_GROUP, KNX_PORT))
        end, seen = time.monotonic() + ctx.timeout, set()
        while time.monotonic() < end:
            sock.settimeout(max(0.05, end - time.monotonic()))
            try:
                data, (ip, _p) = sock.recvfrom(1500)
            except (socket.timeout, OSError):
                break
            info = parse_knx_search_response(data)
            if info and ip not in seen:
                seen.add(ip)
                ctx.emit(Record("KNXnet/IP", ip, info["knx_address"], "device", info["friendly_name"], details=info))
    except OSError as exc:
        ctx.log("knx: %s" % exc)
    finally:
        sock.close()


# ---- OPC UA -----------------------------------------------------------------
def scan_opcua(ctx: Context):
    for host in ctx.open_hosts(4840):
        url = ("opc.tcp://%s:4840" % host).encode()
        body = struct.pack("<IIIII", 0, 65536, 65536, 0, 0) + struct.pack("<I", len(url)) + url
        hello = b"HELF" + struct.pack("<I", 8 + len(body)) + body
        try:
            with socket.create_connection((host, 4840), timeout=ctx.timeout) as s:
                s.sendall(hello)
                reply = s.recv(64)
        except OSError:
            continue
        ctx.emit(Record("OPC UA", host, "", "server", "ACK" if reply[:3] == b"ACK" else "open",
                        details={"endpoint": url.decode(), "note": "node tree needs an OPC UA session"}))


# ---- Siemens S7comm ---------------------------------------------------------
S7_SETUP = bytes.fromhex("0300001902f08032010000000000080000f0000001000101e0")
S7_SZL_0011 = bytes.fromhex("0300002102f080320700000100000800080001120411440100ff09000400110000")


def s7_connect_request(slot):
    return bytes.fromhex("0300001611e00000000100c0010ac1020100c20201") + bytes([slot])


def parse_s7_order_number(data):
    i = data.find(b"6ES7")
    return data[i : i + 20].decode("ascii", "replace").strip("\0 ") if i >= 0 else ""


def scan_s7(ctx: Context):
    for host in ctx.open_hosts(102):
        for slot in (2, 1, 0):
            try:
                with socket.create_connection((host, 102), timeout=ctx.timeout) as s:
                    s.settimeout(ctx.timeout)
                    s.sendall(s7_connect_request(slot))
                    cc = s.recv(64)
                    if len(cc) < 6 or cc[5] != 0xD0:
                        continue
                    s.sendall(S7_SETUP)
                    s.recv(64)
                    s.sendall(S7_SZL_0011)
                    resp = s.recv(512)
            except OSError:
                continue
            order = parse_s7_order_number(resp)
            ctx.emit(Record("Siemens S7", host, "rack0/slot%d" % slot, "device", order,
                            details={"order_number": order, "note": "S7comm TCP/102 (ISO-on-TCP)"}))
            break
        else:
            ctx.emit(Record("Siemens S7", host, "", "iso-tsap", "open", details={"note": "port 102 open, COTP refused"}))


# ---- generic service probe --------------------------------------------------
SERVICES = {
    502: "Modbus TCP", 102: "Siemens S7/ISO-TSAP", 4840: "OPC UA", 1883: "MQTT", 8883: "MQTT TLS",
    20000: "DNP3", 2404: "IEC 60870-5-104", 9600: "OMRON FINS", 34962: "PROFINET IO", 34963: "PROFINET IO",
    34964: "PROFINET CM", 44818: "EtherNet/IP", 47808: "BACnet/IP (tcp)", 4843: "OPC UA TLS",
    18245: "GE SRTP", 1962: "PCWorx", 5007: "MELSEC",
}


def scan_services(ctx: Context):
    for port, name in sorted(SERVICES.items()):
        for host in ctx.open_hosts(port):
            ctx.emit(Record("TCP service", host, "", "port:%d" % port, "open", details={"service": name}))
