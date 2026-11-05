import socket
import struct
import threading
import unittest

from predator.context import Context
from predator.scanners import bacnet


def oid(otype, inst):
    return struct.pack(">I", (otype << 22) | inst)


def text(s):
    raw = b"\x00" + s.encode()
    return (b"\x75" + bytes([len(raw)]) if len(raw) >= 5 else bytes([0x70 | len(raw)])) + raw


def real(v):
    return b"\x44" + struct.pack(">f", v)


def unsigned(v):
    return bytes([0x21, v])


def enum(v):
    return bytes([0x91, v])


def objid(otype, inst):
    return b"\xc4" + oid(otype, inst)


def build_db(dev, name, points):
    """{(otype, inst, prop): encoded value} plus the object list."""
    db = {(8, dev, 77): text(name), (8, dev, 121): unsigned(7)}
    objs = [(8, dev)]
    for otype, inst, pname, value, units in points:
        objs.append((otype, inst))
        db[(otype, inst, 77)] = text(pname)
        db[(otype, inst, 85)] = value
        if units is not None:
            db[(otype, inst, 117)] = enum(units)
    db[(8, dev, 76, 0)] = unsigned(len(objs))
    for i, (t, n) in enumerate(objs, 1):
        db[(8, dev, 76, i)] = objid(t, n)
    return db


DEVICES = {
    1234: build_db(1234, "AHU-1", [(0, 1, "Zone Temp", real(21.5), 62), (5, 2, "Fan Cmd", enum(1), None)]),
    77: build_db(77, "VAV-77", [(0, 9, "Room", real(19.0), 64)]),
}


class FakeBacnet(threading.Thread):
    """Answers Who-Is with a local device and a routed MS/TP device, and ReadProperty from DEVICES."""

    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.stop = False

    def iam(self, dev, npdu):
        apdu = b"\x10\x00\xc4" + oid(8, dev) + b"\x22\x05\xc4\x91\x00\x21\x07"
        body = npdu + apdu
        return b"\x81\x0a" + struct.pack(">H", len(body) + 4) + body

    def run(self):
        while not self.stop:
            try:
                data, addr = self.sock.recvfrom(1500)
            except socket.timeout:
                continue
            if data[:4] == b"\x81\x0b\x00\x0c":
                self.sock.sendto(self.iam(1234, b"\x01\x00"), addr)
                self.sock.sendto(self.iam(77, b"\x01\x08\x00\x05\x01\x09"), addr)
                continue
            ctl = data[5]
            i = 6
            routed = bool(ctl & 0x20)
            if routed:
                i += 3 + data[i + 2] + 1
            apdu = data[i:]
            invoke = apdu[2]
            otype_n, inst_n = struct.unpack(">I", apdu[5:9])[0] >> 22, struct.unpack(">I", apdu[5:9])[0] & 0x3FFFFF
            prop = apdu[10]
            idx = apdu[12] if len(apdu) > 11 and apdu[11] == 0x29 else None
            dev = 77 if routed else 1234
            key = (otype_n, inst_n, prop) if idx is None else (otype_n, inst_n, prop, idx)
            value = DEVICES[dev].get(key)
            npdu = b"\x01\x08\x00\x05\x01\x09" if routed else b"\x01\x00"
            if value is None:
                body = npdu + bytes([0x50, invoke, 0x0C, 0x91, 0x02, 0x91, 0x20])
            else:
                head = b"\x30" + bytes([invoke]) + b"\x0c\x0c" + apdu[5:9] + b"\x19" + bytes([prop])
                if idx is not None:
                    head += b"\x29" + bytes([idx])
                body = npdu + head + b"\x3e" + value + b"\x3f"
            self.sock.sendto(b"\x81\x0a" + struct.pack(">H", len(body) + 4) + body, addr)


class BacnetIpTests(unittest.TestCase):
    def test_discover_and_read_local_and_routed_devices(self):
        dev = FakeBacnet()
        dev.start()
        rows = []
        ctx = Context(emit=rows.append, timeout=1.0, log=lambda m: None)
        client = bacnet.Client(1.0, port=dev.port, bind_port=0)
        try:
            found = client.discover(["127.0.0.1"], 1.0)
            self.assertEqual(sorted(k[1] for k in found), [77, 1234])
            for (ip, inst, _snet, _sadr), (_iam, dest) in found.items():
                bacnet.read_device(client, ip, inst, dest, ctx)
        finally:
            client.close()
            dev.stop = True

        by = {(r.device, r.point): r for r in rows}
        self.assertEqual(by[("1234", "device")].point, "device")
        self.assertEqual(by[("1234", "device")].value, "AHU-1")
        self.assertEqual(by[("1234", "device")].details["vendor"], 7)
        temp = by[("1234", "analog-input:1")]
        self.assertEqual((temp.value, temp.units, temp.details["name"]), (21.5, "degC", "Zone Temp"))
        self.assertEqual(by[("1234", "binary-value:2")].value, 1)
        routed = by[("77", "analog-input:9")]
        self.assertEqual((routed.value, routed.units), (19.0, "degF"))
        self.assertIn("via net 5 mac 09", routed.source)


if __name__ == "__main__":
    unittest.main()
