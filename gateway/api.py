"""Status, Web Command Center, and Control API - stdlib only (no Flask/FastAPI).

Routes:
  GET  /                       - Dark-mode Predator Web Command Center
  GET  /assets/predator-icon.png - Real Predator logo asset
  GET  /status                 - JSON: discovered serial ports + BACnet/IP devices + point values
  GET  /rows                   - JSON: live discovered CSV rows
  GET  /telemetry              - JSON: live Pi hardware/RTC/Ethernet/service telemetry
  GET  /points.json            - JSON: curated points-map with live values
  GET  /ports                  - JSON: serial ports + state + manual overrides + valid choices
  GET  /export.csv             - Download discovered-points CSV
  GET  /export.xlsx            - Download Mirius Excel point map
  GET  /export.icsproj         - Download Mirius ICC Studio project (.icsproj)
  GET  /icc/preview            - JSON: ICC project point preview, MACs, DB pins, Port B config
  POST /icc/config             - Save ICC UI config (Port B, float32, exclusions, MACs) & rebuild
  POST /point/read             - Live on-demand BACnet ReadProperty (MS/TP or BACnet/IP)
  POST /point/write            - Live on-demand BACnet WriteProperty / Relinquish (NULL)
  POST /rescan                 - Force serial re-scan (?port=/dev/ttyUSB0 optional)
  POST /ports/override         - Manually pin protocol/baud/parity on a serial port
  POST /ports/auto             - Clear manual port override back to auto-detect
  POST /bus-doctor             - Start passive RS-485 Bus Doctor sniff job
  GET  /bus-doctor             - Latest Bus Doctor job status / diagnostic report
  POST /recon/start            - Start background multi-protocol site recon
  GET  /recon/status           - Recon progress, logs, and discovered records
  GET  /recon/export.csv       - Download latest multi-protocol recon CSV
  GET  /snapshots              - List saved equipment/site snapshots
  POST /snapshots              - Save current scan as named snapshot
  POST /snapshots/delete       - Delete a saved snapshot
  GET  /snapshots/diff         - Diff ?before=<id>&after=<id|live>
"""

import dataclasses
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import bacnet_ip
from . import bus_doctor
from . import config as cfgmod
from . import demo
from . import discovery
from . import icc_project
from . import mstp
from . import recon
from . import snapshots
from . import telemetry
from .csv_export import build_rows, write_csv
from .points import load_points
from .runtime_state import MODBUS_RTU, MSTP, STATE, UNKNOWN, save_overrides
from .web_ui import WEB_UI_HTML

log = logging.getLogger("predator.api")

_ICON_PATH = os.path.join(os.path.dirname(__file__), "assets", "predator-icon.png")

_BD_LOCK = threading.RLock()
_BD_STATE = {"running": False, "port": None, "report": None, "error": None, "ts": 0.0}


def _start_bus_doctor_job(cfg, port, baud, duration_s):
    with _BD_LOCK:
        if _BD_STATE["running"]:
            return False, "Bus Doctor is already running"
        _BD_STATE.update({"running": True, "port": port, "report": None, "error": None, "ts": time.time()})

    def _worker():
        try:
            bauds = cfgmod.autodetect_scan_bauds(cfg)
            report = bus_doctor.diagnose_port(port, baud=baud or None, duration_s=duration_s, sweep_bauds=bauds)
            with _BD_LOCK:
                _BD_STATE.update({"running": False, "report": report, "error": None, "ts": time.time()})
        except Exception as e:  # noqa: BLE001
            with _BD_LOCK:
                _BD_STATE.update({"running": False, "report": None, "error": str(e), "ts": time.time()})

    threading.Thread(target=_worker, name=f"bus-doctor-{port}", daemon=True).start()
    return True, "started"


def _parse_obj_spec(obj_str):
    s = str(obj_str or "").strip()
    if ":" not in s:
        raise ValueError("object must be 'type:instance', e.g. 'analog-input:1'")
    t, _, inst = s.partition(":")
    return t.strip().lower(), int(inst.strip())


def _resolve_mstp_port(cfg, device_instance, bus_hint=""):
    st, _mac = STATE.find_mstp_device(device_instance)
    if st and st.baud:
        return st.port, st.baud
    if bus_hint and "@" in bus_hint:
        p, _, b = bus_hint.rpartition("@")
        try:
            return p, int(b)
        except ValueError:
            pass
    st = STATE.port_for_protocol(MSTP)
    if st and st.baud:
        return st.port, st.baud
    return cfg["mstp"]["iface"], cfg.getint("mstp", "baud")


def _live_point_read(cfg, body):
    source = str(body.get("source", "")).lower()
    obj_type, obj_inst = _parse_obj_spec(body.get("object"))
    dev_inst = int(body["address"])
    bus_hint = str(body.get("bus", ""))
    if source == "mstp":
        iface, baud = _resolve_mstp_port(cfg, dev_inst, bus_hint)
        val = mstp.read_property(cfg, dev_inst, obj_type, obj_inst, prop="present-value", iface=iface, baud=baud)
        units = ""
        try:
            if obj_type in ("analog-input", "analog-output", "analog-value"):
                units = str(mstp.read_property(cfg, dev_inst, obj_type, obj_inst, prop="units", iface=iface, baud=baud))
        except Exception:  # noqa: BLE001
            pass
        return {"value": val, "units": units, "source": source}
    if source == "bacnet-ip":
        dev = STATE.get_bacnet_ip_device(dev_inst)
        addr = (dev.address if dev else "") or bus_hint
        if not addr:
            raise ValueError(f"no network address known for BACnet/IP device {dev_inst}")
        val = bacnet_ip.live_read_property(addr, obj_type, obj_inst, prop="presentValue")
        units = ""
        try:
            if obj_type in ("analog-input", "analog-output", "analog-value"):
                units = str(bacnet_ip.live_read_property(addr, obj_type, obj_inst, prop="units"))
        except Exception:  # noqa: BLE001
            pass
        return {"value": val, "units": units, "source": source}
    raise ValueError(f"live read not supported for source {source!r}")


def _live_point_write(cfg, body):
    source = str(body.get("source", "")).lower()
    obj_type, obj_inst = _parse_obj_spec(body.get("object"))
    dev_inst = int(body["address"])
    bus_hint = str(body.get("bus", ""))
    value = body.get("value")
    priority = int(body.get("priority", 8))
    if not (1 <= priority <= 16):
        raise ValueError("priority must be 1..16")
    if source == "mstp":
        iface, baud = _resolve_mstp_port(cfg, dev_inst, bus_hint)
        mstp.write_property(cfg, dev_inst, obj_type, obj_inst, value, priority=priority, iface=iface, baud=baud)
        return {"ok": True, "source": source, "priority": priority, "relinquished": value is None}
    if source == "bacnet-ip":
        dev = STATE.get_bacnet_ip_device(dev_inst)
        addr = (dev.address if dev else "") or bus_hint
        if not addr:
            raise ValueError(f"no network address known for BACnet/IP device {dev_inst}")
        bacnet_ip.live_write_property(addr, obj_type, obj_inst, value, priority=priority)
        return {"ok": True, "source": source, "priority": priority, "relinquished": value is None}
    raise ValueError(f"live write not supported for source {source!r}")


def _make_handler(cfg):
    class Handler(BaseHTTPRequestHandler):
        server_version = "PredatorGateway/0.2"

        def log_message(self, fmt, *args):  # noqa: A003
            log.info("%s - %s", self.address_string(), fmt % args)

        def _send_bytes(self, body, content_type, status=200, filename=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, obj, status=200):
            body = json.dumps(obj, default=str, indent=2).encode("utf-8")
            self._send_bytes(body, "application/json", status=status)

        def _read_json_body(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length == 0:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/":
                self._send_bytes(WEB_UI_HTML.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/assets/predator-icon.png":
                self._serve_icon()
            elif path == "/status":
                self._send_json(STATE.snapshot())
            elif path == "/rows":
                self._send_json({"rows": build_rows(cfg), "ts": time.time()})
            elif path == "/telemetry":
                self._send_json(telemetry.collect())
            elif path == "/points.json":
                try:
                    points = load_points(cfgmod.points_map_path(cfg))
                except Exception as e:  # noqa: BLE001
                    self._send_json({"error": str(e)}, status=500)
                    return
                values = STATE.all_point_values()
                for p in points:
                    p["_live"] = values.get(p["name"])
                self._send_json(points)
            elif path == "/export.csv":
                self._serve_csv()
            elif path == "/export.xlsx":
                self._serve_icc_file("xlsx_path", "Predator_Points.xlsx",
                                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            elif path == "/export.icsproj":
                self._serve_icc_file("path", "Predator_Mirius_BACnet.icsproj", "application/xml")
            elif path == "/icc/preview":
                rows = build_rows(cfg)
                self._send_json(icc_project.build_preview(cfg, rows, discovered_macs=STATE.mstp_device_macs()))
            elif path == "/ports":
                self._send_json(_ports_payload(cfg))
            elif path == "/bus-doctor":
                with _BD_LOCK:
                    self._send_json(dict(_BD_STATE))
            elif path == "/recon/status":
                self._send_json(recon.RECON.status())
            elif path == "/recon/export.csv":
                self._serve_recon_csv()
            elif path == "/snapshots":
                self._send_json({"snapshots": snapshots.list_snapshots(cfg)})
            elif path == "/snapshots/diff":
                self._handle_diff(parse_qs(parsed.query))
            else:
                self._send_json({"error": "not found"}, status=404)

        def do_POST(self):  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/rescan":
                port = parse_qs(parsed.query).get("port", [None])[0]
                results = discovery.force_rescan(cfg, port=port)
                self._send_json({"results": results})
            elif path == "/ports/override":
                self._handle_set_override()
            elif path == "/ports/auto":
                self._handle_clear_override()
            elif path == "/icc/config":
                self._handle_icc_config()
            elif path == "/point/read":
                self._handle_point_read()
            elif path == "/point/write":
                self._handle_point_write()
            elif path == "/bus-doctor":
                self._handle_bus_doctor()
            elif path == "/recon/start":
                self._handle_recon_start()
            elif path == "/snapshots":
                self._handle_snapshot_save()
            elif path == "/snapshots/delete":
                self._handle_snapshot_delete()
            elif path == "/demo/load":
                res = demo.load_demo(cfg, bd_state=_BD_STATE, bd_lock=_BD_LOCK, recon_mgr=recon.RECON)
                write_csv(cfg)
                self._send_json(res)
            elif path == "/demo/clear":
                res = demo.clear_demo(cfg, bd_state=_BD_STATE, bd_lock=_BD_LOCK, recon_mgr=recon.RECON)
                write_csv(cfg)
                self._send_json(res)
            else:
                self._send_json({"error": "not found"}, status=404)

        def _serve_icon(self):
            try:
                with open(_ICON_PATH, "rb") as f:
                    self._send_bytes(f.read(), "image/png")
            except OSError:
                self._send_json({"error": "icon not found"}, status=404)

        def _serve_csv(self):
            try:
                path, _ = write_csv(cfg)
                with open(path, "rb") as f:
                    body = f.read()
            except Exception as e:  # noqa: BLE001
                self._send_json({"error": str(e)}, status=500)
                return
            self._send_bytes(body, "text/csv", filename=os.path.basename(path))

        def _serve_icc_file(self, cfg_key, download_name, content_type):
            try:
                rows = build_rows(cfg)
                icc_project.write_if_configured(cfg, rows, discovered_macs=STATE.mstp_device_macs())
                fpath = os.path.expanduser(cfg.get("icc", cfg_key))
                if not os.path.exists(fpath):
                    self._send_json({"error": "no MS/TP points discovered yet to generate this file"}, status=404)
                    return
                with open(fpath, "rb") as f:
                    body = f.read()
            except Exception as e:  # noqa: BLE001
                self._send_json({"error": str(e)}, status=500)
                return
            self._send_bytes(body, content_type, filename=download_name)

        def _serve_recon_csv(self):
            st = recon.RECON.status()
            fpath = st.get("csv_path") or os.path.expanduser(cfg.get("recon", "output_csv", fallback="~/predator_recon.csv"))
            if not os.path.exists(fpath):
                self._send_json({"error": "no recon CSV generated yet"}, status=404)
                return
            with open(fpath, "rb") as f:
                self._send_bytes(f.read(), "text/csv", filename="predator_recon.csv")

        def _handle_icc_config(self):
            try:
                body = self._read_json_body()
                ui_path = cfg.get("icc", "ui_config_path", fallback="~/predator_icc_ui.json")
                icc_project.save_ui_config(ui_path, body)
                rows = build_rows(cfg)
                macs = STATE.mstp_device_macs()
                written = icc_project.write_if_configured(cfg, rows, discovered_macs=macs)
                preview = icc_project.build_preview(cfg, rows, discovered_macs=macs)
                self._send_json({"saved": True, "rebuilt": bool(written), "preview": preview})
            except Exception as e:  # noqa: BLE001
                self._send_json({"error": str(e)}, status=400)

        def _handle_point_read(self):
            try:
                body = self._read_json_body()
                res = _live_point_read(cfg, body)
                self._send_json(res)
            except Exception as e:  # noqa: BLE001
                self._send_json({"error": str(e)}, status=400)

        def _handle_point_write(self):
            try:
                body = self._read_json_body()
                res = _live_point_write(cfg, body)
                self._send_json(res)
            except Exception as e:  # noqa: BLE001
                self._send_json({"error": str(e)}, status=400)

        def _handle_bus_doctor(self):
            try:
                body = self._read_json_body()
                port = str(body.get("port") or "").strip()
                if not port:
                    raise ValueError("port is required")
                baud = int(body.get("baud") or 0)
                duration_s = float(body.get("duration_s") or 5.0)
                ok, msg = _start_bus_doctor_job(cfg, port, baud, duration_s)
                if not ok:
                    self._send_json({"error": msg}, status=409)
                    return
                self._send_json({"accepted": True, "port": port, "baud": baud, "duration_s": duration_s})
            except Exception as e:  # noqa: BLE001
                self._send_json({"error": str(e)}, status=400)

        def _handle_recon_start(self):
            try:
                body = self._read_json_body()
                out_csv = cfg.get("recon", "output_csv", fallback="~/predator_recon.csv")
                res = recon.RECON.start(
                    protocols=body.get("protocols"),
                    subnets=body.get("subnets"),
                    output_csv=out_csv,
                )
                self._send_json(res)
            except Exception as e:  # noqa: BLE001
                self._send_json({"error": str(e)}, status=400)

        def _handle_snapshot_save(self):
            try:
                body = self._read_json_body()
                name = body.get("name") or time.strftime("scan_%Y%m%d_%H%M%S")
                rows = build_rows(cfg)
                snap = snapshots.save_snapshot(cfg, name, rows, meta={"device_macs": STATE.mstp_device_macs()})
                self._send_json(snap)
            except Exception as e:  # noqa: BLE001
                self._send_json({"error": str(e)}, status=400)

        def _handle_snapshot_delete(self):
            try:
                body = self._read_json_body()
                ok = snapshots.delete_snapshot(cfg, body["id"])
                self._send_json({"deleted": ok})
            except Exception as e:  # noqa: BLE001
                self._send_json({"error": str(e)}, status=400)

        def _handle_diff(self, qs):
            try:
                before_id = (qs.get("before") or [""])[0]
                after_id = (qs.get("after") or ["live"])[0]
                if not before_id:
                    raise ValueError("before snapshot id is required")
                before = snapshots.load_snapshot(cfg, before_id)["rows"]
                after = build_rows(cfg) if after_id == "live" else snapshots.load_snapshot(cfg, after_id)["rows"]
                self._send_json(snapshots.diff_rows(before, after))
            except FileNotFoundError as e:
                self._send_json({"error": str(e)}, status=404)
            except Exception as e:  # noqa: BLE001
                self._send_json({"error": str(e)}, status=400)

        def _handle_set_override(self):
            try:
                body = self._read_json_body()
                port = body["port"]
                protocol = body["protocol"]
                baud = int(body["baud"])
                parity = body.get("parity", "N")
                if protocol not in (MSTP, MODBUS_RTU):
                    raise ValueError(f"protocol must be {MSTP!r} or {MODBUS_RTU!r}")
            except (KeyError, ValueError, TypeError) as e:
                self._send_json({"error": f"bad request: {e}"}, status=400)
                return
            STATE.set_override(port, protocol, baud, parity=parity)
            save_overrides(cfgmod.overrides_path(cfg))
            STATE.set_port_state(port, note="applying override - scanning...")
            threading.Thread(
                target=discovery.detect_port_forced,
                args=(cfg, port, protocol, baud), kwargs={"parity": parity},
                name=f"override-{port}", daemon=True,
            ).start()
            self._send_json({"accepted": True, "port": port, "protocol": protocol, "baud": baud})

        def _handle_clear_override(self):
            try:
                port = self._read_json_body()["port"]
            except (KeyError, TypeError) as e:
                self._send_json({"error": f"bad request: {e}"}, status=400)
                return
            STATE.clear_override(port)
            save_overrides(cfgmod.overrides_path(cfg))
            STATE.set_port_state(port, protocol=UNKNOWN, note="back to auto-detect - scanning...")
            threading.Thread(
                target=discovery.force_rescan, args=(cfg,), kwargs={"port": port},
                name=f"rescan-{port}", daemon=True,
            ).start()
            self._send_json({"accepted": True, "port": port})

    return Handler


def _ports_payload(cfg):
    """Everything gateway/viewer.py and web_ui.py need for serial port controls."""
    known = set(discovery.find_serial_ports(cfg))
    states = {s.port: s for s in STATE.all_port_states()}
    overrides = STATE.all_overrides()
    known |= set(states) | set(overrides)

    ports = []
    for port in sorted(known):
        state = states.get(port)
        override = overrides.get(port)
        ports.append({
            "port": port,
            "state": dataclasses.asdict(state) if state else None,
            "override": dataclasses.asdict(override) if override else None,
        })
    return {
        "ports": ports,
        "protocol_choices": [MSTP, MODBUS_RTU],
        "baud_choices": cfgmod.autodetect_scan_bauds(cfg),
        "parity_choices": ["N", "E", "O"],
    }


def start_server(cfg):
    port = cfg.getint("api", "port")
    handler = _make_handler(cfg)
    server = ThreadingHTTPServer(("", port), handler)
    t = threading.Thread(target=server.serve_forever, name="predator-api", daemon=True)
    t.start()
    log.info("API & Web Command Center listening on :%d", port)
    return server
