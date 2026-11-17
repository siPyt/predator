import os
import tempfile
import unittest
import zipfile
from xml.etree import ElementTree as ET

from gateway import icc_project as icc


def _read(path):
    with open(path, "rb") as f:
        return f.read()


def _objects(xml_text):
    root = ET.fromstring(xml_text.encode("utf-8"))
    return [{c.tag: c.text for c in s} for s in root.iter("ServObj")]


class BuildProjectTests(unittest.TestCase):
    def test_object_layout_per_type(self):
        pts = [icc.Point(9050, 1, "AI", 5, "OutdoorAirTemp"), icc.Point(9050, 1, "AV", 7, "Setpoint"),
               icc.Point(9050, 1, "BI", 3, "FlowStatus"), icc.Point(9050, 1, "MV", 2, "Mode")]
        ai, av, bi, mv = _objects(icc.build_project(pts))
        self.assertEqual(ai["VAddr"], "5")
        self.assertNotIn("WFC", ai)
        self.assertEqual(av["VAddr"], str((2 << 22) | 7))
        self.assertEqual((av["WFC"], av["Pri"]), ("15", "16"))
        self.assertEqual((bi["DSize"], bi["Bits"], bi["VAddr"]), ("1", "1", str((3 << 22) | 3)))
        self.assertIn("OB1", mv)
        self.assertNotIn("Mult", mv)
        self.assertEqual(ai["Dest"], "1")
        self.assertEqual(ai["Inst"], "9050")

    def test_database_addresses(self):
        pts = [icc.Point(1, 1, "AI", 1, "a"), icc.Point(1, 1, "BI", 1, "b"), icc.Point(1, 1, "BI", 2, "c"),
               icc.Point(1, 1, "MI", 1, "d"), icc.Point(1, 1, "AV", 9, "e", db_addr=100), icc.Point(1, 1, "AV", 10, "f")]
        icc.allocate_db_addresses(pts)
        # explicit addresses are kept; the rest continue after the highest one in use
        self.assertEqual([p.db_addr for p in pts], [104, 108, 109, 110, 100, 114])

    def test_abbreviations_are_unique_rad50_and_short(self):
        used = set()
        names = ["C1EvapRefPressure", "C1EvapRefPressure", "LvgEvapWaterTempUnit", "a_b-c d", ""]
        out = [icc.abbreviate(n, used) for n in names]
        self.assertEqual(len(set(out)), len(out))
        for s in out:
            self.assertRegex(s, r"^[A-Z0-9]{1,12}$")

    def test_rows_keep_only_mappable_mstp_objects(self):
        rows = [
            {"source": "mstp", "bus_or_network": "/dev/ttyUSB0@19200", "address": "9050",
             "object_or_register": "analog-input:1", "description": "EntEvapWaterTemp"},
            {"source": "mstp", "bus_or_network": "/dev/ttyUSB0@19200", "address": "9050",
             "object_or_register": "calendar:1", "description": "CA1"},
            {"source": "mstp", "bus_or_network": "/dev/ttyUSB0@19200", "address": "9050",
             "object_or_register": "", "description": "no objects"},
            {"source": "bacnet-ip", "bus_or_network": "10.0.0.2", "address": "5",
             "object_or_register": "analog-input:1", "description": "x"},
        ]
        points, baud = icc.points_from_rows(rows, default_mac=6, device_macs={9050: 3})
        self.assertEqual(baud, 19200)
        self.assertEqual([(p.obj_type, p.instance, p.mac) for p in points], [("AI", 1, 3)])

    def test_xlsx_reader_and_write_is_idempotent(self):
        sheet = (
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
            '<row r="1">' + "".join(
                '<c r="%s1" t="inlineStr"><is><t>%s</t></is></c>' % (col, text) for col, text in
                zip("ABCDE", ["Target Device ID", "Object Type", "Object Instance", "Object Name", "ICC Database Address"])
            ) + "</row>"
            '<row r="2"><c r="A2"><v>9050</v></c><c r="B2" t="inlineStr"><is><t>AV</t></is></c>'
            '<c r="C2"><v>4</v></c><c r="D2" t="inlineStr"><is><t>ChillerCapacity</t></is></c><c r="E2"><v>44</v></c></row>'
            "</sheetData></worksheet>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            xlsx = os.path.join(tmp, "p.xlsx")
            with zipfile.ZipFile(xlsx, "w") as z:
                z.writestr("xl/worksheets/sheet1.xml", sheet)
            points = icc.points_from_xlsx(xlsx, default_mac=1)
            self.assertEqual((points[0].device_instance, points[0].obj_type, points[0].instance, points[0].db_addr),
                             (9050, "AV", 4, 44))
            out = os.path.join(tmp, "out.icsproj")
            self.assertTrue(icc.write_project(out, points))
            self.assertFalse(icc.write_project(out, points))


    def test_workbook_round_trips_and_is_deterministic(self):
        pts = icc.allocate_db_addresses(icc.sort_points([
            icc.Point(9050, 1, "BI", 2, "Flow"), icc.Point(9050, 1, "AV", 3, "SetPt"), icc.Point(9050, 1, "AI", 1, "Temp")]))
        data = icc.xlsx_bytes(pts)
        self.assertEqual(data, icc.xlsx_bytes(pts))
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "p.xlsx")
            with open(path, "wb") as f:
                f.write(data)
            back = icc.points_from_xlsx(path)
        self.assertEqual([(p.obj_type, p.instance, p.db_addr) for p in back], [("AI", 1, 0), ("AV", 3, 4), ("BI", 2, 8)])
        self.assertEqual(icc.fln_point_numbers(20)[:3] + icc.fln_point_numbers(20)[-2:], [3, 4, 5, 22, 23])

    def test_gateway_hook_refreshes_and_keeps_edited_desktop_files(self):
        from gateway import config as cfgmod

        rows = [{"source": "mstp", "bus_or_network": "/dev/ttyUSB0@9600", "address": "9050",
                 "object_or_register": "analog-input:1", "description": "EntEvapWaterTemp"}]
        with tempfile.TemporaryDirectory() as tmp:
            cfg = cfgmod.load_config(os.path.join(tmp, "none.conf"))
            for key, name in (("path", "gen.icsproj"), ("xlsx_path", "gen.xlsx")):
                cfg["icc"][key] = os.path.join(tmp, name)
            desk = os.path.join(tmp, "Desktop")
            cfg["icc"]["desktop_copy"] = os.path.join(desk, "mine.icsproj")
            cfg["icc"]["xlsx_desktop_copy"] = os.path.join(desk, "mine.xlsx")

            self.assertTrue(icc.write_if_configured(cfg, rows))
            self.assertIn(b"<Baud>9600</Baud>", _read(cfg["icc"]["path"]))
            self.assertFalse(icc.write_if_configured(cfg, rows))  # unchanged -> nothing rewritten
            first = _read(cfg["icc"]["desktop_copy"])

            # new points appear: both desktop files refresh by themselves
            rows.append(dict(rows[0], object_or_register="analog-input:2"))
            self.assertTrue(icc.write_if_configured(cfg, rows))
            self.assertNotEqual(_read(cfg["icc"]["desktop_copy"]), first)
            self.assertEqual(len(icc.points_from_xlsx(cfg["icc"]["xlsx_desktop_copy"])), 2)
            self.assertEqual(sorted(os.listdir(desk)), ["mine.icsproj", "mine.xlsx"])

            # the engineer saves Port B work over the desktop project; the next refresh keeps it
            with open(cfg["icc"]["desktop_copy"], "wb") as f:
                f.write(b"engineer edits")
            rows.append(dict(rows[0], object_or_register="analog-input:3"))
            self.assertTrue(icc.write_if_configured(cfg, rows))
            kept = [n for n in os.listdir(desk) if "(saved" in n]
            self.assertEqual(len(kept), 1)
            self.assertEqual(_read(os.path.join(desk, kept[0])), b"engineer edits")
            self.assertIn(b"<ServObj>", _read(cfg["icc"]["desktop_copy"]))
            self.assertIsNone(icc.write_if_configured(cfg, []))  # no points -> existing files untouched

if __name__ == "__main__":
    unittest.main()
