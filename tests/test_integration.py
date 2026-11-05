import socket
import struct
import threading
import unittest

from predator.context import Context, SerialSpec
from predator.scanners import bacnet, modbus, mqtt, profibus


def serve_once(handler):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def run():
        conn, _ = srv.accept()
        try:
            handler(conn)
        except OSError:
            pass
        finally:
            conn.close()
            srv.close()

    threading.Thread(target=run, daemon=True).start()
    return srv.getsockname()[1]


def hold_open(conn, seconds=4):
    conn.settimeout(seconds)
    try:
        conn.recv(1)
    except OSError:
        pass


def oid(otype, inst):
    return struct.pack(">I", (otype << 22) | inst)


def mstp_frame(ftype, dest, src, data=b""):
    hdr = bytes([ftype, dest, src, len(data) >> 8, len(data) & 0xFF])
    out = b"\x55\xff" + hdr + bytes([bacnet.mstp_header_crc(hdr)])
    return out + (data + struct.pack("<H", bacnet.mstp_data_crc(data)) if data else b"")


NPDU = b"\x01\x00"
RP_ACK = b"\x30\x07\x0c\x0c" + oid(0, 3) + b"\x19\x55\x3e\x44" + struct.pack(">f", 21.5) + b"\x3f"
RPM_ACK = (b"\x30\x08\x0e\x0c" + oid(1, 4) + b"\x1e\x29\x55\x4e\x44" + struct.pack(">f", 7.5) + b"\x4f\x1f")
COV = (b"\x10\x02\x09\x01\x1c" + oid(8, 100) + b"\x2c" + oid(3, 7) + b"\x39\x00\x4e\x09\x55\x2e\x91\x01\x2f\x4f")


class PassiveDecodeTests(unittest.TestCase):
    def test_apdu_points(self):
        self.assertEqual(bacnet.decode_apdu_points(RP_ACK), [("analog-input", 3, "present-value", 21.5)])
        self.assertEqual(bacnet.decode_apdu_points(RPM_ACK), [("analog-output", 4, "present-value", 7.5)])
        self.assertEqual(bacnet.decode_apdu_points(COV), [("binary-input", 7, "present-value", 1)])
        self.assertEqual(bacnet.decode_apdu_points(b"\x00\x05\x01\x0c"), [])


class SerialScannerTests(unittest.TestCase):
    def test_mstp_passive_capture_to_csv_rows(self):
        iam = b"\x10\x00\xc4" + oid(8, 1234) + b"\x22\x05\xc4\x91\x03\x21\x07"
        stream = (mstp_frame(0, 4, 3) + mstp_frame(5, 1, 3, NPDU + RP_ACK) + mstp_frame(6, 1, 3, NPDU + RPM_ACK)
                  + mstp_frame(6, 255, 3, NPDU + COV) + mstp_frame(6, 255, 4, NPDU + iam)
                  + mstp_frame(6, 1, 3, NPDU + RP_ACK))
        port = serve_once(lambda c: (c.sendall(b"\x00\x13" + stream), hold_open(c)))
        rows = []
        ctx = Context(emit=rows.append, sniff_seconds=1.5)
        bacnet.scan_serial(ctx, SerialSpec("socket://127.0.0.1:%d" % port, "bacnet-mstp", [38400]))
        got = {(r.device, r.point): r.value for r in rows}
        self.assertEqual(got[("3", "station")], "token")
        self.assertEqual(got[("3", "analog-input:3")], 21.5)
        self.assertEqual(got[("3", "analog-output:4")], 7.5)
        self.assertEqual(got[("3", "binary-input:7")], 1)
        self.assertEqual(got[("4", "device")], 1234)
        self.assertEqual(sum(1 for r in rows if r.point == "analog-input:3"), 1)

    def test_profibus_passive_capture(self):
        def sd1(da, sa, fc):
            return bytes([0x10, da, sa, fc, (da + sa + fc) & 0xFF, 0x16])

        body = bytes([0x02, 0x03, 0x08, 0x01, 0x02])
        sd2 = bytes([0x68, len(body), len(body), 0x68]) + body + bytes([sum(body) & 0xFF, 0x16])
        port = serve_once(lambda c: (c.sendall(sd1(3, 2, 0x49) + sd2 + b"\xdc\x02\x02"), hold_open(c)))
        rows = []
        ctx = Context(emit=rows.append, sniff_seconds=1.5)
        profibus.scan_serial(ctx, SerialSpec("socket://127.0.0.1:%d" % port, "profibus", [93750]))
        got = {(r.device, r.point): r.value for r in rows}
        self.assertEqual(got[("2", "station")], "master")
        self.assertEqual(got[("3", "station")], "slave")
        self.assertEqual(got[("3", "process-data-in")], "0102")

    def test_modbus_rtu_scan(self):
        def slave(conn):
            conn.settimeout(5)
            while True:
                req = conn.recv(64)
                if not req:
                    return
                unit, fc = req[0], req[1]
                if unit != 1:
                    continue
                if fc == 0x2B:
                    resp = bytes([unit, fc | 0x80, 1])
                else:
                    start, count = struct.unpack(">HH", req[2:6])
                    data = bytes((count + 7) // 8) if fc in (1, 2) else b"".join(struct.pack(">H", 100 + start + i) for i in range(count))
                    resp = bytes([unit, fc, len(data)]) + data
                conn.sendall(resp + struct.pack("<H", modbus.crc16(resp)))

        port = serve_once(slave)
        rows = []
        ctx = Context(emit=rows.append, modbus_units=[1], modbus_registers=2)
        modbus.scan_serial(ctx, SerialSpec("socket://127.0.0.1:%d" % port, "modbus-rtu", [9600], "N"))
        got = {r.point: r.value for r in rows}
        self.assertEqual(got["holding:0"], 100)
        self.assertEqual(got["input:1"], 101)
        self.assertEqual(got["coil:0"], 0)


def recv_exact(conn, n):
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise OSError("closed")
        buf += chunk
    return buf


class MqttTests(unittest.TestCase):
    def test_collects_json_from_fake_broker(self):
        def broker(conn):
            conn.settimeout(8)
            while True:
                first = recv_exact(conn, 2)
                body = recv_exact(conn, first[1])
                kind = first[0] >> 4
                if kind == 1:
                    conn.sendall(b"\x20\x02\x00\x00")
                elif kind == 8:
                    conn.sendall(b"\x90\x04" + body[:2] + b"\x00\x00")
                    topic, payload = b"plant/a", b'{"temp": 21.5, "pump": {"on": true}}'
                    conn.sendall(b"\x30" + bytes([2 + len(topic) + len(payload)]) + struct.pack(">H", len(topic)) + topic + payload)
                elif kind == 12:
                    conn.sendall(b"\xd0\x00")
                elif kind == 14:
                    return

        port = serve_once(broker)
        rows = []
        ctx = Context(emit=rows.append, mqtt_seconds=2)
        mqtt.collect("127.0.0.1", port, ctx)
        got = {r.point: r.value for r in rows}
        self.assertEqual(got["plant/a/temp"], 21.5)
        self.assertEqual(got["plant/a/pump/on"], True)
        self.assertEqual(got["summary"], 1)


if __name__ == "__main__":
    unittest.main()
