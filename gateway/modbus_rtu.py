"""Modbus RTU access via pymodbus.

Modbus RTU's timing is far more tolerant than BACnet MS/TP's token-passing, so
(unlike the MS/TP trunk) this talks to the bus directly in Python without needing
a compiled helper.
"""

import struct
import threading
import time

from pymodbus.client import ModbusSerialClient

_lock = threading.Lock()
_client = None
_client_key = None


class ModbusError(RuntimeError):
    pass


def _get_client(cfg):
    """One shared serial connection, reopened only if the config changed."""
    global _client, _client_key
    m = cfg["modbus"]
    key = (m["iface"], m["baud"], m["parity"], m["stopbits"], m["bytesize"])
    with _lock:
        if _client is not None and _client_key == key:
            return _client
        if _client is not None:
            try:
                _client.close()
            except Exception:  # noqa: BLE001
                pass
        _client = ModbusSerialClient(
            port=m["iface"],
            baudrate=int(m["baud"]),
            parity=m["parity"],
            stopbits=int(m["stopbits"]),
            bytesize=int(m["bytesize"]),
            timeout=float(m["timeout_s"]),
        )
        _client_key = key
        return _client


def _decode(registers, data_type):
    if data_type == "uint16":
        return registers[0]
    if data_type == "int16":
        v = registers[0]
        return v - 0x10000 if v >= 0x8000 else v
    raw = struct.pack(">HH", registers[0], registers[1])
    if data_type == "uint32":
        return struct.unpack(">I", raw)[0]
    if data_type == "int32":
        return struct.unpack(">i", raw)[0]
    if data_type == "float32":
        return struct.unpack(">f", raw)[0]
    raise ModbusError(f"unknown data_type {data_type}")


def _register_count(data_type):
    return 1 if data_type in ("uint16", "int16") else 2


def read_point(cfg, modbus_spec, timeout=None):
    """modbus_spec: {"unit", "register_type", "address", "data_type", "scale"}"""
    client = _get_client(cfg)
    unit = modbus_spec["unit"]
    address = modbus_spec["address"]
    data_type = modbus_spec.get("data_type", "uint16")
    scale = modbus_spec.get("scale", 1.0)
    count = _register_count(data_type)

    with _lock:
        if not client.connect():
            raise ModbusError(f"cannot open {cfg['modbus']['iface']}")
        rtype = modbus_spec["register_type"]
        if rtype == "holding":
            rr = client.read_holding_registers(address=address, count=count, device_id=unit)
        elif rtype == "input":
            rr = client.read_input_registers(address=address, count=count, device_id=unit)
        elif rtype == "coil":
            rr = client.read_coils(address=address, count=1, device_id=unit)
        elif rtype == "discrete":
            rr = client.read_discrete_inputs(address=address, count=1, device_id=unit)
        else:
            raise ModbusError(f"unknown register_type {rtype}")

    if rr is None or rr.isError():
        raise ModbusError(str(rr) if rr is not None else "no response")
    if rtype in ("coil", "discrete"):
        return bool(rr.bits[0])
    return _decode(rr.registers, data_type) * scale


def write_point(cfg, modbus_spec, value):
    client = _get_client(cfg)
    unit = modbus_spec["unit"]
    address = modbus_spec["address"]
    rtype = modbus_spec["register_type"]
    data_type = modbus_spec.get("data_type", "uint16")
    scale = modbus_spec.get("scale", 1.0)

    with _lock:
        if not client.connect():
            raise ModbusError(f"cannot open {cfg['modbus']['iface']}")
        if rtype == "coil":
            rr = client.write_coil(address=address, value=bool(value), device_id=unit)
        elif rtype == "holding":
            raw = value / scale if scale else value
            if data_type in ("uint16", "int16"):
                rr = client.write_register(address=address, value=int(raw) & 0xFFFF, device_id=unit)
            else:
                packed = struct.pack(">f" if data_type == "float32" else ">I", raw if data_type == "float32" else int(raw))
                hi, lo = struct.unpack(">HH", packed)
                rr = client.write_registers(address=address, values=[hi, lo], device_id=unit)
        else:
            raise ModbusError(f"register_type {rtype} is not writable")

    if rr is None or rr.isError():
        raise ModbusError(str(rr) if rr is not None else "no response")
    return True


def probe(cfg, unit, register_type, address, count=1, timeout=None):
    """Raw read for discovery/mapping assistance - no scaling/decoding applied."""
    client = _get_client(cfg)
    with _lock:
        if not client.connect():
            raise ModbusError(f"cannot open {cfg['modbus']['iface']}")
        if register_type == "holding":
            rr = client.read_holding_registers(address=address, count=count, device_id=unit)
        elif register_type == "input":
            rr = client.read_input_registers(address=address, count=count, device_id=unit)
        else:
            raise ModbusError(f"unsupported register_type {register_type} for probe")
    if rr is None or rr.isError():
        raise ModbusError(str(rr) if rr is not None else "no response")
    return rr.registers
