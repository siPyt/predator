import socket
import struct
import threading
import unittest

from predator.context import Context
from predator.scanners import bacnet, modbus, mqtt, others, profibus, profinet


def obj_id(otype, inst):
    return struct.pack(">I", (otype << 22) | inst)


class ModbusTests(unittest.TestCase):
    def test_crc_known_vector(self):
        self.assertEqual(modbus.crc16(bytes.fromhex("010300000001")), 0x0A84)

    def test_tcp_scan_against_fake_server(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def serve():
            conn, _ = srv.accept()
            while True:
                hdr = conn.recv(7)
                if len(hdr) < 7:
                    return
                tid, _p, ln, unit = struct.unpack(">HHHB", hdr)
                pdu = conn.recv(ln - 1)
                fc = pdu[0]
                if unit != 1 or fc == 0x2B:
                    resp = bytes([fc | 0x80, 1])
                else:
                    start, count = struct.unpack(">HH", pdu[1:5])
                    if fc in (1, 2):
                        data = bytes((count + 7) // 8)
                    else:
                        data = b"".join(struct.pack(">H", start + i) for i in range(count))
                    resp = bytes([fc, len(data)]) + data
                conn.sendall(struct.pack(">HHHB", tid, 0, len(resp) + 1, unit) + resp)

        threading.Thread(target=serve, daemon=True).start()
        rows = []
        ctx = Context(emit=rows.append, modbus_units=[1, 2], modbus_registers=4)
        t = modbus.TcpTransport("127.0.0.1", port, 1.0)
        self.assertTrue(modbus.scan_unit(t, 1, "127.0.0.1", "Modbus TCP", ctx))
        t.close()
        holding = {r.point: r.value for r in rows if r.point.startswith("holding:")}
        self.assertEqual(holding, {"holding:0": 0, "holding:1": 1, "holding:2": 2, "holding:3": 3})


class BacnetTests(unittest.TestCase):
    def test_who_is_frame(self):
        self.assertEqual(bacnet.WHO_IS[:4], b"\x81\x0b\x00\x0c")

    def test_parse_iam(self):
        apdu = b"\x10\x00\xc4" + obj_id(8, 1234) + b"\x22\x05\xc4" + b"\x91\x03" + b"\x21\x07"
        self.assertEqual(
            bacnet.parse_iam(apdu),
            {"object_type": 8, "instance": 1234, "max_apdu": 1476, "segmentation": 3, "vendor_id": 7},
        )

    def test_npdu_with_source(self):
        npdu = bytes([1, 0x08]) + struct.pack(">HB", 5, 1) + b"\x09"
        self.assertEqual(bacnet.parse_npdu(npdu), ({"snet": 5, "sadr": "09"}, 6))

    def test_read_property_ack_real(self):
        apdu = b"\x30\x07\x0c\x0c" + obj_id(0, 3) + b"\x19\x55\x3e\x44" + struct.pack(">f", 21.5) + b"\x3f"
        self.assertEqual(bacnet.parse_read_ack(apdu, 7), [21.5])

    def test_read_property_ack_string_and_objid(self):
        name = b"\x75\x06\x00ZoneT"
        apdu = b"\x30\x01\x0c\x0c" + obj_id(8, 9) + b"\x19\x4d\x3e" + name + b"\x3f"
        self.assertEqual(bacnet.parse_read_ack(apdu, 1), ["ZoneT"])
        apdu = b"\x30\x02\x0c\x0c" + obj_id(8, 9) + b"\x19\x4c\x29\x01\x3e\xc4" + obj_id(2, 10) + b"\x3f"
        self.assertEqual(bacnet.parse_read_ack(apdu, 2), ["analog-value:10"])

    def test_error_ack_returns_none(self):
        self.assertIsNone(bacnet.parse_read_ack(b"\x50\x01\x0c\x91\x02\x91\x20", 1))

    def test_mstp_crc_residue_and_roundtrip(self):
        hdr = bytes([6, 0xFF, 0x05, 0x00, 0x04])
        data = b"\x01\x00\x10\x08"
        frame = (b"\x55\xff" + hdr + bytes([bacnet.mstp_header_crc(hdr)]) + data
                 + struct.pack("<H", bacnet.mstp_data_crc(data)))
        frames, rest = bacnet.parse_mstp(b"\x00\x00" + frame + frame[:5])
        self.assertEqual(frames, [(6, 0xFF, 5, data)])
        self.assertEqual(rest, frame[:5])
        self.assertEqual(bacnet.mstp_header_crc(hdr + bytes([bacnet.mstp_header_crc(hdr)])), 0xAA)


class ProfibusTests(unittest.TestCase):
    def test_telegrams(self):
        sd1 = bytes([0x10, 0x03, 0x82, 0x49, (0x03 + 0x82 + 0x49) & 0xFF, 0x16])
        du = b"\x01\x02"
        body = bytes([0x03, 0x02, 0x7D]) + du
        sd2 = bytes([0x68, len(body), len(body), 0x68]) + body + bytes([sum(body) & 0xFF, 0x16])
        token = b"\xdc\x01\x01"
        out, rest = profibus.parse_telegrams(b"\xe5" + sd1 + sd2 + token + b"\x10\x01")
        self.assertEqual(out[0], (0x10, 3, 0x82, 0x49, b""))
        self.assertEqual(out[1], (0x68, 3, 2, 0x7D, du))
        self.assertEqual(out[2][0], 0xDC)
        self.assertEqual(rest, b"\x10\x01")

    def test_bad_checksum_is_skipped(self):
        out, _ = profibus.parse_telegrams(bytes([0x10, 1, 2, 3, 0x00, 0x16]))
        self.assertEqual(out, [])


class ProfinetTests(unittest.TestCase):
    def test_identify_frame(self):
        frame = profinet.build_identify_all(bytes(6))
        self.assertEqual(len(frame), 60)
        self.assertEqual(frame[:6], bytes.fromhex("010ecf000000"))
        self.assertEqual(frame[12:16], b"\x88\x92\xfe\xfe")

    def test_parse_response(self):
        def block(opt, sub, val):
            data = b"\x00\x00" + val if (opt, sub) != (2, 4) else b"\x00\x00" + val
            b = bytes([opt, sub]) + struct.pack(">H", len(data)) + data
            return b + (b"\0" if len(data) & 1 else b"")

        blocks = (block(2, 2, b"plc-1") + block(2, 3, struct.pack(">HH", 0x002A, 0x0301))
                  + block(1, 2, socket.inet_aton("10.0.0.5") + socket.inet_aton("255.255.255.0") + socket.inet_aton("10.0.0.1")))
        frame = (bytes(6) + bytes.fromhex("aabbccddeeff") + b"\x88\x92\xfe\xff\x05\x01" + bytes(4) + bytes(2)
                 + struct.pack(">H", len(blocks)) + blocks)
        info = profinet.parse_identify_response(frame)
        self.assertEqual(info["name_of_station"], "plc-1")
        self.assertEqual((info["vendor_id"], info["device_id"]), ("0x002a", "0x0301"))
        self.assertEqual(info["ip"], "10.0.0.5")
        self.assertEqual(info["mac"], "aa:bb:cc:dd:ee:ff")


class MqttTests(unittest.TestCase):
    def test_json_is_flattened(self):
        rows = mqtt.message_rows("plant/a", b'{"temp": 21.5, "pump": {"on": true}}')
        self.assertEqual({p: v for p, v, _ in rows}, {"plant/a/temp": 21.5, "plant/a/pump/on": True})

    def test_plain_and_binary_and_sparkplug(self):
        self.assertEqual(mqtt.message_rows("t", b"42")[0][:2], ("t", "42"))
        self.assertEqual(mqtt.message_rows("t", b"\xff\xfe")[0][1], "fffe")
        self.assertIn("sparkplug", mqtt.message_rows("spBv1.0/g/DDATA/n", b"\x08\x01")[0][2]["encoding"])


class OtherTests(unittest.TestCase):
    def test_s7_frames_have_correct_tpkt_length(self):
        for frame in (others.S7_SETUP, others.S7_SZL_0011, others.s7_connect_request(2)):
            self.assertEqual(struct.unpack(">H", frame[2:4])[0], len(frame))

    def test_s7_order_number(self):
        self.assertEqual(others.parse_s7_order_number(b"xx6ES7 214-1AG40-0XB0 \0\0"), "6ES7 214-1AG40-0XB0")

    def test_enip_identity(self):
        item = struct.pack("<H", 1) + bytes(16) + struct.pack("<HHHBBHI", 1, 12, 99, 2, 5, 0, 0xDEADBEEF)
        item += bytes([5]) + b"Micro" + b"\x03"
        data = struct.pack("<HHIIQI", 0x63, 0, 0, 0, 0, 0) + struct.pack("<HHH", 1, 0x0C, len(item)) + item
        info = others.parse_list_identity(data)
        self.assertEqual((info["product_name"], info["revision"], info["vendor_id"]), ("Micro", "2.5", 1))

    def test_knx_response(self):
        dib = bytes([54, 1, 2, 0]) + struct.pack(">H", 0x1101) + bytes(12) + bytes.fromhex("010203040506") + b"Router".ljust(30, b"\0")
        data = b"\x06\x10\x02\x02" + struct.pack(">H", 14 + len(dib)) + bytes(8) + dib
        info = others.parse_knx_search_response(data)
        self.assertEqual((info["knx_address"], info["friendly_name"]), ("1.1.1", "Router"))


if __name__ == "__main__":
    unittest.main()
