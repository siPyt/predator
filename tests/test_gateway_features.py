import os
import struct
import tempfile
import unittest
from xml.etree import ElementTree as ET

from gateway import bus_doctor
from gateway import config as cfgmod
from gateway import demo
from gateway import icc_project as icc
from gateway import mstp
from gateway import snapshots
from gateway import telemetry
from gateway import web_ui
from predator.scanners.bacnet import mstp_data_crc, mstp_header_crc


def _mstp_frame(ftype, dest, src, data=b""):
    hdr = bytes([ftype, dest, src, (len(data) >> 8) & 0xFF, len(data) & 0xFF])
    frame = b"\x55\xFF" + hdr + bytes([mstp_header_crc(hdr)])
    if data:
        crc = mstp_data_crc(data)
        frame += data + bytes([crc & 0xFF, (crc >> 8) & 0xFF])
    return frame


class MstpEnhancementTests(unittest.TestCase):
    def test_parse_devices_and_macs_from_bacwi(self):
        sample = """
;Device   MAC (hex)            SNET  SADR (hex)           APDU
;-------  -------------------- ----- -------------------- ----
  9050    01                   0     00                   480
  3200    0A                   0     00                   1476
  7777    7F                   0     00                   480
;
; Total Devices: 3
"""
        devices, macs = mstp._parse_devices_and_macs(sample)
        self.assertEqual(devices, [9050, 3200, 7777])
        self.assertEqual(macs, {9050: 1, 3200: 10, 7777: 127})

    def test_parse_epics_objects_with_values_units_and_writable(self):
        sample = """
{
  Object_Identifier: (analog-input, 1)
  Object_Name: "EntEvapWaterTemp"
  Present_Value: 44.500000
  Units: degrees-fahrenheit
  Description: "Entering Chilled Water"
}

{
  Object_Identifier: analog-value, 4
  Object_Name: "ChillerSetpoint"
  Present_Value: 42.0
  Units: degrees-fahrenheit
  Priority_Array: {Null, Null}
}

{
  Object_Identifier: (binary-input, 2)
  Object_Name: "FlowSwitch"
  Present_Value: active
}
"""
        objs = mstp.parse_epics_objects(sample)
        self.assertEqual(len(objs), 3)
        self.assertEqual(objs[0]["type"], "analog-input")
        self.assertEqual(objs[0]["instance"], 1)
        self.assertEqual(objs[0]["name"], "EntEvapWaterTemp")
        self.assertEqual(objs[0]["value"], 44.5)
        self.assertEqual(objs[0]["units"], "degrees-fahrenheit")
        self.assertFalse(objs[0]["writable"])
        self.assertTrue(objs[1]["writable"])
        self.assertEqual(objs[2]["value"], "active")
        self.assertFalse(objs[2]["writable"])

    def test_bacwp_tags_for_all_types_and_relinquish(self):
        self.assertEqual(mstp._bacwp_tag_and_value("analog-value", 72.5), ("4", "72.5"))
        self.assertEqual(mstp._bacwp_tag_and_value("binary-output", "active"), ("9", "1"))
        self.assertEqual(mstp._bacwp_tag_and_value("binary-value", False), ("9", "0"))
        self.assertEqual(mstp._bacwp_tag_and_value("multi-state-value", 3), ("2", "3"))
        self.assertEqual(mstp._bacwp_tag_and_value("analog-value", None), ("0", "0"))
        self.assertEqual(mstp._bacwp_tag_and_value("analog-value", "null"), ("0", "0"))


class IccEnhancedTests(unittest.TestCase):
    def test_db_address_pinning_keeps_existing_addresses_stable(self):
        pts1 = icc.sort_points([
            icc.Point(9050, 1, "AI", 1, "Temp1"),
            icc.Point(9050, 1, "AI", 5, "Temp5"),
            icc.Point(9050, 1, "BI", 1, "Flow"),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            db_map_path = os.path.join(tmp, "db.json")
            icc.allocate_db_addresses(pts1)
            icc.save_db_map(db_map_path, pts1)
            orig = {icc.point_key(p): p.db_addr for p in pts1}

            # Insert a brand-new AI:2 right in the middle of sort order
            pts2 = icc.sort_points([
                icc.Point(9050, 1, "AI", 1, "Temp1"),
                icc.Point(9050, 1, "AI", 2, "NewMiddleTemp"),
                icc.Point(9050, 1, "AI", 5, "Temp5"),
                icc.Point(9050, 1, "BI", 1, "Flow"),
            ])
            pinned = icc.load_db_map(db_map_path)
            icc.allocate_db_addresses(pts2, pinned_map=pinned)
            after = {icc.point_key(p): p.db_addr for p in pts2}
            for k, v in orig.items():
                self.assertEqual(after[k], v)
            # NewMiddleTemp gets appended after BI:1 (addr 8 + 1 = 9)
            self.assertEqual(after["9050:AI:2"], 9)

    def test_port_b_siemens_fln_slave_xml_matches_icc_schema(self):
        pts = [
            icc.Point(9050, 5, "AI", 1, "EntEvapWaterTemp", value="44.5", units="degrees-fahrenheit", use_float=True),
            icc.Point(9050, 5, "AV", 2, "ChilledWaterSP", value="42.0", units="degrees-fahrenheit"),
            icc.Point(9050, 5, "BI", 1, "FlowStatus"),
            icc.Point(9050, 5, "BO", 2, "EnableCmd"),
        ]
        xml = icc.build_project(pts, baud=38400, include_port_b=True, fln_baud=9600, fln_addr=7, fln_app_num=2766)
        root = ET.fromstring(xml.encode("utf-8"))
        ports = list(root.iter("Port"))
        self.assertEqual(len(ports), 2)
        self.assertEqual(ports[0].findtext("Index"), "1")
        self.assertEqual(ports[1].findtext("Index"), "0")
        self.assertEqual(ports[1].findtext("Proto/ID"), "18")
        self.assertEqual(ports[1].findtext("Proto/Baud"), "9600")
        self.assertEqual(ports[1].findtext("Proto/Node/Addr"), "07")

        # When Port B FLN is enabled, Port A analog stays 16-bit unsigned to match FLN
        serv_objs = [{c.tag: c.text for c in s} for s in ports[0].iter("ServObj")]
        self.assertEqual(serv_objs[0]["Float"], "0")
        self.assertEqual(serv_objs[0]["Dest"], "5")

        fln_objs = [{c.tag: c.text for c in s} for s in ports[1].iter("FlnObj")]
        self.assertEqual(len(fln_objs), 4)
        # LAI (3), LAO (4), LDI (1), LDO (2) with FLN point numbers 3, 4, 5, 6
        self.assertEqual(fln_objs[0]["VAddr"], str((3 << 16) | 3))
        self.assertEqual(fln_objs[0]["Units"], "DEGF")
        self.assertEqual(fln_objs[0]["DBAddr"], serv_objs[0]["DBAddr"])
        self.assertEqual(fln_objs[1]["VAddr"], str((4 << 16) | 4))
        self.assertEqual(fln_objs[2]["VAddr"], str((1 << 16) | 5))
        self.assertEqual(fln_objs[2]["Mask"], "1")
        self.assertEqual(fln_objs[3]["VAddr"], str((2 << 16) | 6))

    def test_port_b_bacnet_mstp_server_xml_matches_icc_schema(self):
        pts = [
            icc.Point(9050, 1, "AI", 1, "EntEvapWaterTemp", value="54.2", units="degrees-fahrenheit", use_float=True),
            icc.Point(9050, 1, "AV", 2, "ChilledWaterSP", value="44.0", units="degrees-fahrenheit"),
            icc.Point(9050, 1, "BI", 1, "FlowStatus"),
            icc.Point(9050, 1, "BO", 1, "EnableCmd"),
            icc.Point(9050, 1, "MV", 1, "Mode"),
        ]
        xml = icc.build_project(
            pts, baud=19200, port_b_protocol="bacnet-mstp",
            port_b_baud=38400, port_b_mac=10, port_b_device_instance=10000, port_b_device_name="Mirius_B",
        )
        root = ET.fromstring(xml.encode("utf-8"))
        ports = list(root.iter("Port"))
        self.assertEqual(len(ports), 2)
        self.assertEqual(ports[0].findtext("Index"), "1")
        self.assertEqual(ports[1].findtext("Index"), "0")
        self.assertEqual(ports[1].findtext("Proto/ID"), "3")
        self.assertEqual(ports[1].findtext("Proto/Baud"), "38400")
        self.assertEqual(ports[1].findtext("Proto/Node/Addr"), "10")
        self.assertEqual(ports[1].findtext("Proto/Node/InstNum"), "10000")
        self.assertEqual(ports[1].findtext("Proto/Node/DevName"), "Mirius_B")

        serv_objs = [{c.tag: c.text for c in s} for s in ports[0].iter("ServObj")]
        bn_objs = [{c.tag: c.text for c in s} for s in ports[1].iter("BNObj")]
        self.assertEqual(len(bn_objs), 5)
        # AI:1 uses 32-bit float on BOTH Port A and Port B, matching DBAddr and degF (64)
        self.assertEqual((serv_objs[0]["Float"], serv_objs[0]["DSize"]), ("1", "4"))
        self.assertEqual((bn_objs[0]["Signed"], bn_objs[0]["Float"], bn_objs[0]["DSize"]), ("1", "1", "4"))
        self.assertEqual(bn_objs[0]["DBAddr"], serv_objs[0]["DBAddr"])
        self.assertEqual(bn_objs[0]["Units"], "64")
        # AV:2 writable gets RDVal=0; BO:1 gets Pol=0 & RDVal=0; MV:1 gets NStates=65535 & RDVal=1
        self.assertEqual(bn_objs[1]["RDVal"], "0")
        self.assertEqual((bn_objs[3]["Pol"], bn_objs[3]["RDVal"]), ("0", "0"))
        self.assertEqual((bn_objs[4]["NStates"], bn_objs[4]["RDVal"]), ("65535", "1"))

    def test_port_b_modbus_rtu_and_metasys_n2_xml(self):
        pts = [
            icc.Point(9050, 1, "AI", 1, "EntTemp", value="54.2", use_float=True),
            icc.Point(9050, 1, "AV", 1, "Setpt", value="44.0"),
            icc.Point(9050, 1, "BI", 1, "Flow"),
            icc.Point(9050, 1, "BO", 1, "Enable"),
        ]
        # Modbus RTU Slave (ID 2, DefMap=1)
        mb_xml = icc.build_project(pts, port_b_protocol="modbus-rtu", modbus_baud=19200, modbus_parity=2, modbus_addr=15)
        mb_root = ET.fromstring(mb_xml.encode("utf-8"))
        mb_port_b = list(mb_root.iter("Port"))[1]
        self.assertEqual((mb_port_b.findtext("Index"), mb_port_b.findtext("Proto/ID")), ("0", "2"))
        self.assertEqual((mb_port_b.findtext("Proto/Baud"), mb_port_b.findtext("Proto/Par")), ("19200", "2"))
        self.assertEqual((mb_port_b.findtext("Proto/Node/Addr"), mb_port_b.findtext("Proto/Node/DefMap")), ("15", "1"))

        # Metasys N2 Slave (ID 12, fixed 9600/8N1, N2Obj AI/AO/BI/BO)
        n2_xml = icc.build_project(pts, port_b_protocol="metasys-n2", n2_addr=22)
        n2_root = ET.fromstring(n2_xml.encode("utf-8"))
        n2_port_b = list(n2_root.iter("Port"))[1]
        self.assertEqual((n2_port_b.findtext("Index"), n2_port_b.findtext("Proto/ID")), ("0", "12"))
        self.assertEqual((n2_port_b.findtext("Proto/Baud"), n2_port_b.findtext("Proto/Node/Addr")), ("9600", "22"))
        n2_objs = [{c.tag: c.text for c in s} for s in n2_port_b.iter("N2Obj")]
        self.assertEqual(len(n2_objs), 4)
        self.assertEqual(n2_objs[0]["VAddr"], str((1 << 16) | 1))  # AI:1
        self.assertEqual((n2_objs[0]["Float"], n2_objs[0]["DSize"]), ("1", "4"))
        self.assertEqual(n2_objs[1]["VAddr"], str((3 << 16) | 1))  # AO:1
        self.assertEqual(n2_objs[2]["VAddr"], str((2 << 16) | 1))  # BI:1
        self.assertEqual(n2_objs[3]["VAddr"], str((4 << 16) | 1))  # BO:1

    def test_port_a_float32_enabled_when_port_b_off(self):
        pts = [icc.Point(9050, 1, "AI", 1, "Temp", value="44.25", use_float=True)]
        self.assertTrue(icc.suggest_float(pts[0]))
        xml = icc.build_project(pts, include_port_b=False)
        root = ET.fromstring(xml.encode("utf-8"))
        so = {c.tag: c.text for c in next(root.iter("ServObj"))}
        self.assertEqual((so["Float"], so["DSize"], so["Bytes"]), ("1", "4", "4"))

    def test_discovered_macs_used_automatically_in_gateway_hook(self):
        rows = [{"source": "mstp", "bus_or_network": "/dev/ttyUSB0@19200", "address": "9050",
                 "object_or_register": "analog-input:1", "description": "Temp", "value": "44.5", "units": "degF"}]
        with tempfile.TemporaryDirectory() as tmp:
            cfg = cfgmod.load_config(os.path.join(tmp, "none.conf"))
            cfg["icc"]["path"] = os.path.join(tmp, "gen.icsproj")
            cfg["icc"]["desktop_copy"] = ""
            cfg["icc"]["xlsx_path"] = os.path.join(tmp, "gen.xlsx")
            cfg["icc"]["xlsx_desktop_copy"] = ""
            cfg["icc"]["db_map_path"] = os.path.join(tmp, "db.json")
            cfg["icc"]["ui_config_path"] = os.path.join(tmp, "ui.json")
            self.assertTrue(icc.write_if_configured(cfg, rows, discovered_macs={9050: 12}))
            with open(cfg["icc"]["path"], "rb") as f:
                self.assertIn(b"<Dest>12</Dest>", f.read())


class BusDoctorTests(unittest.TestCase):
    def test_silent_and_garbage_bus(self):
        rep_silent = bus_doctor.analyze_mstp_bytes(b"", elapsed_s=3.0, baud=38400)
        self.assertEqual(rep_silent["health"], "silent")
        rep_noise = bus_doctor.analyze_mstp_bytes(b"\x12\x34\x56\x78" * 40, elapsed_s=3.0, baud=38400)
        self.assertEqual(rep_noise["health"], "error")
        self.assertEqual(rep_noise["valid_frames"], 0)

    def test_healthy_mstp_token_ring_and_iam(self):
        # NPDU (ver=1, ctl=0) + Unconfirmed I-Am for device 9050, max_apdu=480, seg=3, vendor=7
        iam_apdu = (
            b"\x10\x00"
            + b"\xc4" + struct.pack(">I", (8 << 22) | 9050)
            + b"\x22\x01\xe0"
            + b"\x91\x03"
            + b"\x21\x07"
        )
        npdu = b"\x01\x00" + iam_apdu
        raw = (
            _mstp_frame(0, 5, 1)
            + _mstp_frame(0, 1, 5)
            + _mstp_frame(1, 2, 1)
            + _mstp_frame(6, 255, 5, npdu)
        )
        rep = bus_doctor.analyze_mstp_bytes(raw, elapsed_s=2.0, baud=19200)
        self.assertEqual(rep["health"], "healthy")
        self.assertEqual(rep["valid_frames"], 4)
        self.assertEqual(rep["header_crc_errors"], 0)
        self.assertEqual(rep["tokens_per_sec"], 1.0)
        self.assertEqual([m["mac"] for m in rep["active_masters"]], [1, 5])
        self.assertEqual(rep["iam_devices"][9050]["mac"], 5)


class SnapshotsAndTelemetryTests(unittest.TestCase):
    def test_save_list_diff_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = cfgmod.load_config(os.path.join(tmp, "none.conf"))
            cfg["snapshots"]["dir"] = os.path.join(tmp, "snaps")
            rows_before = [
                {"source": "mstp", "address": "9050", "object_or_register": "analog-input:1", "description": "Temp", "value": "44.0", "units": "degF"},
                {"source": "mstp", "address": "9050", "object_or_register": "binary-input:1", "description": "OldPt", "value": "inactive", "units": ""},
            ]
            rows_after = [
                {"source": "mstp", "address": "9050", "object_or_register": "analog-input:1", "description": "Temp", "value": "46.5", "units": "degF"},
                {"source": "mstp", "address": "9050", "object_or_register": "analog-input:2", "description": "NewPt", "value": "55.0", "units": "degF"},
            ]
            s = snapshots.save_snapshot(cfg, "Chiller 1", rows_before)
            self.assertEqual(s["id"], "Chiller_1")
            self.assertEqual(len(snapshots.list_snapshots(cfg)), 1)
            diff = snapshots.diff_rows(snapshots.load_snapshot(cfg, "Chiller_1")["rows"], rows_after)
            self.assertEqual(len(diff["added"]), 1)
            self.assertEqual(len(diff["removed"]), 1)
            self.assertEqual(len(diff["changed"]), 1)
            self.assertEqual(diff["changed"][0]["changes"]["value"], {"before": "44.0", "after": "46.5"})
            self.assertTrue(snapshots.delete_snapshot(cfg, "Chiller_1"))
            self.assertEqual(snapshots.list_snapshots(cfg), [])

    def test_telemetry_and_web_ui_branding(self):
        t = telemetry.collect()
        self.assertIn("hostname", t)
        self.assertIn("rtc", t)
        self.assertIn("eth0", t)
        self.assertIn("/assets/predator-icon.png", web_ui.WEB_UI_HTML)
        self.assertIn("PREDATOR", web_ui.WEB_UI_HTML)
        self.assertIn("toggleDemo", web_ui.WEB_UI_HTML)

    def test_demo_load_and_clear(self):
        from gateway.runtime_state import STATE
        with tempfile.TemporaryDirectory() as tmp:
            cfg = cfgmod.load_config(os.path.join(tmp, "none.conf"))
            cfg["snapshots"]["dir"] = os.path.join(tmp, "snaps")
            res = demo.load_demo(cfg)
            self.assertTrue(res["demo"])
            self.assertEqual(STATE.mstp_device_macs().get(9050), 1)
            self.assertIsNotNone(STATE.get_bacnet_ip_device(3200))
            self.assertEqual(len(snapshots.list_snapshots(cfg)), 1)
            demo.clear_demo(cfg)
            self.assertIsNone(STATE.get_port_state("/dev/ttyUSB0"))
            self.assertIsNone(STATE.get_bacnet_ip_device(3200))
            self.assertEqual(snapshots.list_snapshots(cfg), [])


if __name__ == "__main__":
    unittest.main()
