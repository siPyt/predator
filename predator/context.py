import ipaddress
import re
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .records import Record

MAX_HOSTS = 4096


@dataclass
class SerialSpec:
    port: str
    protocol: str
    bauds: List[int] = field(default_factory=list)
    parity: str = ""


@dataclass
class Context:
    emit: Callable[[Record], None]
    log: Callable[[str], None] = lambda msg: print(msg, file=sys.stderr)
    subnets: List[str] = field(default_factory=list)
    iface: Optional[str] = None
    timeout: float = 3.0
    sniff_seconds: float = 20.0
    max_points: int = 500
    modbus_units: List[int] = field(default_factory=lambda: list(range(1, 11)))
    modbus_registers: int = 32
    mqtt_brokers: List[Tuple[str, int]] = field(default_factory=list)
    mqtt_user: Optional[str] = None
    mqtt_password: Optional[str] = None
    mqtt_seconds: float = 20.0
    serial: List[SerialSpec] = field(default_factory=list)
    rs485: bool = False
    _open: Dict[int, List[str]] = field(default_factory=dict)

    def hosts(self):
        out = []
        for net in self.subnets:
            for ip in ipaddress.ip_network(net, strict=False).hosts():
                out.append(str(ip))
                if len(out) >= MAX_HOSTS:
                    return out
        return out

    def broadcasts(self):
        return [str(ipaddress.ip_network(n, strict=False).broadcast_address) for n in self.subnets]

    def open_hosts(self, port):
        if port not in self._open:
            hosts = self.hosts()
            with ThreadPoolExecutor(max_workers=128) as pool:
                flags = list(pool.map(lambda h: tcp_open(h, port, 0.5), hosts))
            self._open[port] = [h for h, ok in zip(hosts, flags) if ok]
        return self._open[port]


def tcp_open(host, port, timeout):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def detect_subnets():
    """Local IPv4 subnets (Linux `ip` command); empty list when unavailable."""
    try:
        text = subprocess.run(
            ["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    nets = []
    for line in text.splitlines():
        m = re.match(r"\d+:\s+(\S+)\s+inet\s+(\S+)", line)
        if m and m.group(1) != "lo":
            nets.append(str(ipaddress.ip_interface(m.group(2)).network))
    return nets


def parse_units(text):
    out = []
    for part in text.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out
