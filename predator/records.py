import csv
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

FIELDS = ["timestamp", "protocol", "source", "device", "point", "value", "units", "details"]


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Record:
    protocol: str
    source: str
    device: str = ""
    point: str = ""
    value: object = ""
    units: str = ""
    details: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=_now)

    def row(self):
        return [
            self.timestamp,
            self.protocol,
            self.source,
            self.device,
            self.point,
            "" if self.value is None else self.value,
            self.units,
            json.dumps(self.details, sort_keys=True, default=str) if self.details else "",
        ]


class CsvSink:
    """Thread-safe CSV writer that flushes every row so a power loss keeps the data."""

    def __init__(self, path):
        self._fh = open(path, "w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._fh)
        self._lock = threading.Lock()
        self._writer.writerow(FIELDS)
        self.count = 0

    def write(self, record):
        with self._lock:
            self._writer.writerow(record.row())
            self._fh.flush()
            self.count += 1

    def close(self):
        with self._lock:
            self._fh.close()
