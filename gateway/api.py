"""Minimal status/download API - stdlib only (no Flask/FastAPI dependency,
since predator may be installing packages with no internet access).

Routes:
  GET /            - tiny HTML index with links to the others
  GET /status      - JSON: discovered serial ports + BACnet/IP devices + point values
  GET /points.json - JSON: the curated points-map with live values
  GET /export.csv  - downloads gateway/csv_export.py's current CSV
  POST /rescan     - force an immediate serial re-scan (optional query: ?port=/dev/ttyUSB0)

This is how "download the CSV to the PC" works: browse to
http://<predator-ip>:8099/export.csv (or `curl -O`) from any machine on the
same link - no separate file-transfer setup needed.
"""

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
from .runtime_state import STATE

log = logging.getLogger("predator.api")

_INDEX_HTML = """<!doctype html>
<html><head><title>predator gateway</title></head>
<body>
<h1>predator gateway</h1>
<ul>
<li><a href="/status">/status</a> - discovered ports/devices (JSON)</li>
<li><a href="/points.json">/points.json</a> - curated points map + live values (JSON)</li>
<li><a href="/export.csv">/export.csv</a> - download the discovered-points CSV</li>
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
            else:
                self._send_json({"error": "not found"}, status=404)

        def do_POST(self):  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/rescan":
                port = parse_qs(parsed.query).get("port", [None])[0]
                results = discovery.force_rescan(cfg, port=port)
                self._send_json({"results": results})
            else:
                self._send_json({"error": "not found"}, status=404)

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


def start_server(cfg):
    port = cfg.getint("api", "port")
    handler = _make_handler(cfg)
    server = ThreadingHTTPServer(("", port), handler)
    t = threading.Thread(target=server.serve_forever, name="predator-api", daemon=True)
    t.start()
    log.info("API listening on :%d", port)
    return server
