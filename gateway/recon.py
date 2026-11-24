"""Background runner for the predator multi-protocol CLI scanner (predator/).

Lets the Web Command Center and Tkinter Explorer trigger a full site recon
(BACnet/IP, Modbus TCP, MQTT, PROFINET DCP, EtherNet/IP, S7, OPC UA, KNX,
industrial TCP ports) without needing SSH, streaming status and results to
JSON + CSV.
"""

import dataclasses
import os
import threading
import time
import traceback

from predator.context import Context, detect_subnets, parse_units
from predator.records import CsvSink
from predator.scanners import IP_SCANNERS


class ReconManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._running = False
        self._started_at = 0.0
        self._finished_at = 0.0
        self._protocols = []
        self._current = ""
        self._subnets = []
        self._logs = []
        self._records = []
        self._error = None
        self._csv_path = None

    def start(self, protocols=None, subnets=None, output_csv=None, timeout=2.5, max_points=200):
        with self._lock:
            if self._running:
                return {"started": False, "message": "recon already running"}
            wanted = list(IP_SCANNERS) if (not protocols or protocols == ["all"]) else [
                p for p in protocols if p in IP_SCANNERS
            ]
            if not wanted:
                raise ValueError("no valid protocols selected; choices: " + ", ".join(IP_SCANNERS))
            self._running = True
            self._started_at = time.time()
            self._finished_at = 0.0
            self._protocols = wanted
            self._current = "initializing"
            self._subnets = list(subnets) if subnets else detect_subnets()
            self._logs = []
            self._records = []
            self._error = None
            self._csv_path = os.path.expanduser(output_csv or "~/predator_recon.csv")

        t = threading.Thread(
            target=self._run,
            args=(wanted, list(self._subnets), self._csv_path, float(timeout), int(max_points)),
            name="predator-recon",
            daemon=True,
        )
        t.start()
        return {"started": True, "protocols": wanted, "subnets": self._subnets}

    def _run(self, wanted, subnets, csv_path, timeout, max_points):
        sink = None
        try:
            os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
            sink = CsvSink(csv_path)

            def _emit(rec):
                sink.write(rec)
                with self._lock:
                    if len(self._records) < 2000:
                        self._records.append({
                            "protocol": rec.protocol,
                            "source": rec.source,
                            "device": rec.device,
                            "point": rec.point,
                            "value": rec.value,
                            "units": rec.units,
                            "details": rec.details,
                        })

            def _log(msg):
                with self._lock:
                    self._logs.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
                    if len(self._logs) > 200:
                        self._logs = self._logs[-200:]

            ctx = Context(
                emit=_emit,
                subnets=subnets,
                timeout=timeout,
                max_points=max_points,
                modbus_units=parse_units("1-10,247"),
                modbus_registers=16,
                mqtt_seconds=8.0,
                log=_log,
            )
            _log("Subnets: " + (", ".join(subnets) or "none"))
            for name in wanted:
                with self._lock:
                    self._current = name
                _log(f"Running {name}...")
                try:
                    IP_SCANNERS[name](ctx)
                except Exception as exc:  # noqa: BLE001
                    _log(f"{name} skipped/failed: {exc}")
            _log(f"Complete: {sink.count} record(s) written to {csv_path}")
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._error = f"{exc}\n{traceback.format_exc()}"
        finally:
            if sink is not None:
                try:
                    sink.close()
                except Exception:  # noqa: BLE001
                    pass
            with self._lock:
                self._running = False
                self._current = ""
                self._finished_at = time.time()

    def status(self):
        with self._lock:
            return {
                "running": self._running,
                "current": self._current,
                "protocols": list(self._protocols),
                "available_protocols": list(IP_SCANNERS),
                "subnets": list(self._subnets),
                "started_at": self._started_at,
                "finished_at": self._finished_at,
                "record_count": len(self._records),
                "records": list(self._records),
                "logs": list(self._logs),
                "error": self._error,
                "csv_path": self._csv_path,
            }


RECON = ReconManager()
