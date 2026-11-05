"""Thread-safe shared state for whatever predator has figured out at runtime.

gateway/discovery.py (serial auto-detect + BACnet/IP Who-Is) is the only writer.
gateway/csv_export.py, gateway/api.py, and the points-map poller in
gateway/service.py are readers: they ask "what's on this port right now?" /
"what BACnet/IP devices have we seen?" instead of trusting the static config
file, since the whole point of auto-detect is that iface/baud/protocol are
discovered, not configured.

Everything here is plain dicts/dataclasses behind one lock - this process has
a handful of ports and devices, not thousands, so a single lock is simpler and
plenty fast.
"""

import dataclasses
import threading
import time

UNKNOWN = "unknown"
MSTP = "mstp"
MODBUS_RTU = "modbus-rtu"


@dataclasses.dataclass
class PortState:
    port: str
    protocol: str = UNKNOWN          # "mstp" | "modbus-rtu" | "unknown"
    baud: int = 0
    parity: str = "N"
    stopbits: int = 1
    bytesize: int = 8
    devices: list = dataclasses.field(default_factory=list)  # mstp device instances or modbus unit ids
    last_scan: float = 0.0
    last_confirmed: float = 0.0
    note: str = ""

    def as_row_dicts(self):
        """One dict per discovered device/unit on this port, for the CSV."""
        rows = []
        for d in self.devices:
            rows.append({
                "source": self.protocol if self.protocol != UNKNOWN else "serial-unknown",
                "bus_or_network": self.port,
                "address": d,
                "baud": self.baud if self.protocol != UNKNOWN else "",
            })
        return rows


@dataclasses.dataclass
class BacnetIpDevice:
    device_instance: int
    address: str
    max_apdu: int = 0
    segmentation: str = ""
    vendor_id: int = 0
    device_name: str = ""
    objects: list = dataclasses.field(default_factory=list)  # [{"type":..,"instance":..,"name":..,"value":..}]
    last_seen: float = 0.0


class RuntimeState:
    def __init__(self):
        self._lock = threading.RLock()
        self._ports = {}         # port path -> PortState
        self._bacnet_ip = {}     # device_instance -> BacnetIpDevice
        self._point_values = {}  # point name -> {"value", "error", "ts"}

    # ---- serial ports ----

    def set_port_state(self, port, **kwargs):
        with self._lock:
            state = self._ports.get(port) or PortState(port=port)
            for k, v in kwargs.items():
                setattr(state, k, v)
            self._ports[port] = state
            return state

    def get_port_state(self, port):
        with self._lock:
            return self._ports.get(port)

    def all_port_states(self):
        with self._lock:
            return list(self._ports.values())

    def remove_port(self, port):
        with self._lock:
            self._ports.pop(port, None)

    def port_for_protocol(self, protocol):
        """First confirmed port carrying `protocol` - used by the points-map
        poller to resolve a "mstp"/"modbus" source point to an actual
        iface/baud without the config file needing to know either."""
        with self._lock:
            for state in self._ports.values():
                if state.protocol == protocol:
                    return state
            return None

    # ---- BACnet/IP ----

    def upsert_bacnet_ip_device(self, device_instance, address, **kwargs):
        with self._lock:
            dev = self._bacnet_ip.get(device_instance) or BacnetIpDevice(
                device_instance=device_instance, address=address,
            )
            dev.address = address
            dev.last_seen = time.time()
            for k, v in kwargs.items():
                setattr(dev, k, v)
            self._bacnet_ip[device_instance] = dev
            return dev

    def all_bacnet_ip_devices(self):
        with self._lock:
            return list(self._bacnet_ip.values())

    # ---- points-map live values (written by the poller in service.py) ----

    def set_point_value(self, name, value=None, error=None):
        with self._lock:
            self._point_values[name] = {"value": value, "error": error, "ts": time.time()}

    def get_point_value(self, name):
        with self._lock:
            return self._point_values.get(name)

    def all_point_values(self):
        with self._lock:
            return dict(self._point_values)

    # ---- snapshot for the API/CSV ----

    def snapshot(self):
        with self._lock:
            return {
                "ports": [dataclasses.asdict(s) for s in self._ports.values()],
                "bacnet_ip": [dataclasses.asdict(d) for d in self._bacnet_ip.values()],
                "point_values": dict(self._point_values),
            }


# One process-wide instance; every module imports this same object.
STATE = RuntimeState()
