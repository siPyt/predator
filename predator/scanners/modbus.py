"""Modbus TCP and RTU scanner: device identification plus coil/discrete/holding/input tables."""
import socket
import struct

from ..context import Context, SerialSpec
from ..records import Record
from ..serialutil import open_serial

PORT = 502
TABLES = {1: "coil", 2: "discrete", 3: "holding", 4: "input"}
EXCEPTIONS = {
    1: "illegal function", 2: "illegal address", 3: "illegal value", 4: "device failure",
    5: "acknowledge", 6: "device busy", 8: "memory parity", 10: "gateway path", 11: "gateway target",
}


def crc16(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


class ModbusError(Exception):
    def __init__(self, code):
        super().__init__(EXCEPTIONS.get(code, "exception %d" % code))
        self.code = code


class TcpTransport:
    def __init__(self, host, port=PORT, timeout=1.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(min(timeout, 1.0))
        self.tid = 0

    def _recv(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise OSError("closed")
            buf += chunk
        return buf

    def request(self, unit, pdu):
        self.tid = (self.tid + 1) & 0xFFFF
        self.sock.sendall(struct.pack(">HHHB", self.tid, 0, len(pdu) + 1, unit) + pdu)
        try:
            for _ in range(8):  # skip stale replies left over from earlier timed-out requests
                hdr = self._recv(7)
                tid, proto, length, runit = struct.unpack(">HHHB", hdr)
                if proto != 0 or length < 2 or length > 260:
                    return None
                body = self._recv(length - 1)
                if tid == self.tid and runit == unit:
                    return body
            return None
        except (OSError, socket.timeout):
            return None

    def close(self):
        self.sock.close()


class RtuTransport:
    def __init__(self, port, baud, parity="N", timeout=0.3, rs485=False):
        self.ser = open_serial(port, baud, parity, timeout, rs485)
        self.ser.inter_byte_timeout = 0.03

    def request(self, unit, pdu):
        frame = bytes([unit]) + pdu
        self.ser.reset_input_buffer()
        self.ser.write(frame + struct.pack("<H", crc16(frame)))
        resp = self.ser.read(256)
        if len(resp) < 5 or resp[0] != unit or crc16(resp[:-2]) != struct.unpack("<H", resp[-2:])[0]:
            return None
        return resp[1:-2]

    def close(self):
        self.ser.close()


def _check(resp, fc):
    if resp is None:
        return None
    if resp[0] == fc | 0x80:
        raise ModbusError(resp[1] if len(resp) > 1 else 0)
    return resp if resp[0] == fc else None


def read_table(t, unit, fc, start, count):
    """Returns a list of ints/bools, None on timeout, raises ModbusError on exception response."""
    resp = _check(t.request(unit, struct.pack(">BHH", fc, start, count)), fc)
    if resp is None or len(resp) < 2:
        return None
    data = resp[2 : 2 + resp[1]]
    if fc in (1, 2):
        return [bool(data[i // 8] >> (i % 8) & 1) for i in range(count) if i // 8 < len(data)]
    return list(struct.unpack(">%dH" % (len(data) // 2), data))


def read_device_id(t, unit):
    """FC 43 / MEI 14 basic device identification: {object id: text}."""
    out, next_id = {}, 0
    for _ in range(8):
        resp = _check(t.request(unit, bytes([0x2B, 0x0E, 0x01, next_id])), 0x2B)
        if resp is None or len(resp) < 7 or resp[1] != 0x0E:
            break
        more, next_id, count = resp[4], resp[5], resp[6]
        i = 7
        for _ in range(count):
            if i + 2 > len(resp):
                break
            oid, ln = resp[i], resp[i + 1]
            out[oid] = resp[i + 2 : i + 2 + ln].decode("ascii", "replace")
            i += 2 + ln
        if not more:
            break
    return out


IDENT_NAMES = {0: "vendor", 1: "product_code", 2: "revision", 3: "vendor_url", 4: "product_name", 5: "model", 6: "app_name"}


def scan_unit(t, unit, source, protocol, ctx):
    try:
        ident = read_device_id(t, unit)
        responded = bool(ident)
    except ModbusError:
        ident, responded = {}, True
    found = responded
    rows = []
    for fc, table in TABLES.items():
        count = ctx.modbus_registers if fc in (3, 4) else min(ctx.modbus_registers, 16)
        for start in range(0, count, 8):
            n = min(8, count - start)
            try:
                vals = read_table(t, unit, fc, start, n)
            except ModbusError:
                found = True
                break
            if vals is None:
                break
            found = True
            rows.extend((table, start + i, v) for i, v in enumerate(vals))
    if not found:
        return False
    ident_named = {IDENT_NAMES.get(k, str(k)): v for k, v in ident.items()}
    ctx.emit(Record(protocol, source, str(unit), "device", ident_named.get("product_name", ""), details=ident_named))
    for table, addr, v in rows:
        ctx.emit(Record(protocol, source, str(unit), "%s:%d" % (table, addr), v if not isinstance(v, bool) else int(v)))
    return True


def scan(ctx: Context):
    for host in ctx.open_hosts(PORT):
        try:
            t = TcpTransport(host, PORT, ctx.timeout)
        except OSError:
            continue
        try:
            for unit in ctx.modbus_units:
                scan_unit(t, unit, host, "Modbus TCP", ctx)
        except Exception as exc:
            ctx.log("modbus %s failed: %r" % (host, exc))
        finally:
            t.close()


def scan_serial(ctx: Context, spec: SerialSpec):
    for baud in spec.bauds or [9600, 19200]:
        for parity in (spec.parity,) if spec.parity else ("N", "E"):
            ctx.log("modbus-rtu %s %d baud parity %s" % (spec.port, baud, parity))
            t = RtuTransport(spec.port, baud, parity, rs485=ctx.rs485)
            try:
                hits = sum(scan_unit(t, u, spec.port, "Modbus RTU", ctx) for u in ctx.modbus_units)
            finally:
                t.close()
            if hits:
                return
