"""Minimal status/download API - stdlib only (no Flask/FastAPI dependency,
since predator may be installing packages with no internet access).

Routes:
  GET /              - tiny HTML index with links to the others
  GET /status        - JSON: discovered serial ports + BACnet/IP devices + point values
  GET /points.json   - JSON: the curated points-map with live values
  GET /export.csv    - downloads gateway/csv_export.py's current CSV
  GET /ports         - JSON: every serial port + its current state/override + the
                       valid baud/protocol choices - what gateway/viewer.py's
                       explorer tab renders and lets you override from
  POST /rescan       - force an immediate serial re-scan (optional query: ?port=/dev/ttyUSB0)
  POST /ports/override - body {"port","protocol","baud","parity"?} - manually pin a
                         port's protocol/baud for troubleshooting (persisted)
  POST /ports/auto     - body {"port"} - clear a manual override, back to auto-detect

This is how "download the CSV to the PC" works: browse to
http://<predator-ip>:8099/export.csv (or `curl -O`) from any machine on the
same link - no separate file-transfer setup needed.
"""

import dataclasses
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import config as cfgmod
from . import discovery
from .csv_export import write_csv
from .points import load_points
from .runtime_state import STATE, MSTP, MODBUS_RTU, UNKNOWN, save_overrides

log = logging.getLogger("predator.api")

_INDEX_HTML = """<!doctype html>
<html><head><title>predator gateway</title></head>
<body>
<h1>predator gateway</h1>
<ul>
<li><a href="/status">/status</a> - discovered ports/devices (JSON)</li>
<li><a href="/points.json">/points.json</a> - curated points map + live values (JSON)</li>
<li><a href="/export.csv">/export.csv</a> - download the discovered-points CSV</li>
<li><a href="/ports">/ports</a> - serial ports + overrides (JSON)</li>
</ul>
</body></html>
"""


def _make_handler(cfg):
    class Handler(BaseHTTPRequestHandler):
        server_version = "PredatorGateway/0.1"

        def log_message(self, fmt, *args):  # noqa: A003
            log.info("%s - %s", self.address_string(), fmt % args)

        def _send_json(self, obj, status=200):
            body = json.dumps(obj, default=str, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json_body(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length == 0:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/":
                body = _INDEX_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif parsed.path == "/status":
                self._send_json(STATE.snapshot())
            elif parsed.path == "/points.json":
                try:
                    points = load_points(cfgmod.points_map_path(cfg))
                except Exception as e:  # noqa: BLE001
                    self._send_json({"error": str(e)}, status=500)
                    return
                values = STATE.all_point_values()
                for p in points:
                    p["_live"] = values.get(p["name"])
                self._send_json(points)
            elif parsed.path == "/export.csv":
                self._serve_csv()
            elif parsed.path == "/ports":
                self._send_json(_ports_payload(cfg))
            else:
                self._send_json({"error": "not found"}, status=404)

        def do_POST(self):  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/rescan":
                port = parse_qs(parsed.query).get("port", [None])[0]
                results = discovery.force_rescan(cfg, port=port)
                self._send_json({"results": results})
            elif parsed.path == "/ports/override":
                self._handle_set_override()
            elif parsed.path == "/ports/auto":
                self._handle_clear_override()
            else:
                self._send_json({"error": "not found"}, status=404)

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
            # Run in the background: a Modbus unit sweep can take up to a
            # minute, and this request is called synchronously from
            # gateway/viewer.py's main GUI thread - blocking here would
            # freeze the whole explorer window until the scan finished. The
            # GUI's periodic GET /ports refresh picks up the result instead.
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

        def _serve_csv(self):
            try:
                path, _ = write_csv(cfg)
                with open(path, "rb") as f:
                    body = f.read()
            except Exception as e:  # noqa: BLE001
                self._send_json({"error": str(e)}, status=500)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Disposition", f'attachment; filename="{os.path.basename(path)}"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def _ports_payload(cfg):
    """Everything gateway/viewer.py's explorer tab needs: every port that
    currently exists, its live state, any manual override, and the valid
    choices so the GUI doesn't have to hardcode them."""
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
    log.info("API listening on :%d", port)
    return server
