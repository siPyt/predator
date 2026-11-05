"""Configuration loading for the predator gateway.

Reads one INI file (default: ~/predator_gateway.conf) with sane defaults, so the
service can start even before the file exists. Serial device paths default to
USB-to-RS485 adapters (/dev/ttyUSB*), not the Pi's GPIO UARTs.

The [mstp]/[modbus] iface/baud/parity values below are a MANUAL OVERRIDE/fallback
only. By default ([autodetect] enabled=true) predator does not need to be told
which protocol or baud rate is plugged into which port: gateway/discovery.py scans
whatever /dev/ttyUSB*/ttyACM* ports exist, figures out BACnet MS/TP vs Modbus RTU
(and the baud rate) on its own, and updates gateway/runtime_state.py at runtime.
Set [autodetect] enabled=false and fill in [mstp]/[modbus] to pin a known setup.
"""

import configparser
import os

DEFAULT_CONFIG_PATH = os.path.expanduser(
    os.environ.get("PREDATOR_GATEWAY_CONFIG", "~/predator_gateway.conf")
)

_DEFAULTS = {
    "mstp": {
        # BACnet MS/TP trunk, read via the bacnet-stack CLI tools (bacrp/bacwp/bacwi/bacepics).
        # iface/baud here are only used when [autodetect] enabled=false.
        "enabled": "true",
        "iface": "/dev/ttyUSB0",
        "baud": "38400",
        "our_mac": "100",
        "max_master": "127",
        "max_info_frames": "1",
        "apdu_timeout_ms": "3000",
        "bacnet_bin": "~/bacnet-stack/bin",
    },
    "modbus": {
        # Modbus RTU trunk, read directly via pymodbus.
        # iface/baud/parity here are only used when [autodetect] enabled=false.
        "enabled": "true",
        "iface": "/dev/ttyUSB1",
        "baud": "19200",
        "parity": "N",
        "stopbits": "1",
        "bytesize": "8",
        "timeout_s": "2.0",
    },
    "bacnet_ip": {
        # The virtual BACnet/IP device predator exposes mapped points on.
        # vendor_id 999 is ASHRAE's reserved "unregistered/testing" id -
        # verified live that bacpypes3 crashes on from_args() for any other
        # vendor id unless that id's object/property classes are also fully
        # registered, so leave this at 999 unless you've registered a real one.
        "device_instance": "599100",
        "device_name": "PredatorGateway",
        "vendor_id": "999",
        "bind": "",
        "port": "47808",
        # Periodic global Who-Is interval used for passive BACnet/IP discovery (seconds).
        "discovery_whois_seconds": "30",
    },
    "autodetect": {
        # Figures out, per serial port, whether it's carrying BACnet MS/TP or
        # Modbus RTU and at what baud rate - see gateway/discovery.py.
        "enabled": "true",
        "port_glob": "/dev/ttyUSB*,/dev/ttyACM*",
        "scan_bauds": "9600,19200,38400,57600,76800,115200",
        "rescan_seconds": "45",
        "mstp_probe_timeout_s": "6",
        # Fast pass: a handful of common unit IDs tried first at each baud.
        "modbus_quick_units": "1,2,3,4,5,10,20,32,47,100,247",
        # Deep pass (only run if the quick pass + MS/TP both find nothing):
        # sweep every unit id from 1 up to this value.
        "modbus_deep_scan_max_unit": "247",
        "modbus_probe_timeout_s": "0.25",
    },
    "csv": {
        # Where discovered points/devices get written for the GUI/API/download.
        "path": "~/predator_discovered.csv",
        "desktop_copy": "~/Desktop/predator_discovered.csv",
        "write_seconds": "5",
    },
    "api": {
        "port": "8099",
    },
    "files": {
        "points_map": "~/predator_points.json",
    },
}


def _apply_defaults(cfg):
    for section, values in _DEFAULTS.items():
        if not cfg.has_section(section):
            cfg.add_section(section)
        for key, value in values.items():
            if not cfg.has_option(section, key):
                cfg.set(section, key, value)


def load_config(path=None):
    """Load the gateway config, filling in any missing values with defaults."""
    cfg = configparser.ConfigParser()
    _apply_defaults(cfg)
    path = os.path.expanduser(path or DEFAULT_CONFIG_PATH)
    if os.path.exists(path):
        cfg.read(path)
        _apply_defaults(cfg)  # re-fill anything the file didn't override
    return cfg


def points_map_path(cfg):
    return os.path.expanduser(cfg["files"]["points_map"])


def bacnet_bin_dir(cfg):
    return os.path.expanduser(cfg["mstp"]["bacnet_bin"])


def getboolean(cfg, section, key):
    return cfg.getboolean(section, key)


def _csv_list(value):
    return [v.strip() for v in value.split(",") if v.strip()]


def _int_csv_list(value):
    return [int(v) for v in _csv_list(value)]


def autodetect_enabled(cfg):
    return cfg.getboolean("autodetect", "enabled")


def serial_port_globs(cfg):
    return _csv_list(cfg["autodetect"]["port_glob"])


def autodetect_scan_bauds(cfg):
    return _int_csv_list(cfg["autodetect"]["scan_bauds"])


def autodetect_modbus_quick_units(cfg):
    return _int_csv_list(cfg["autodetect"]["modbus_quick_units"])


def autodetect_modbus_deep_max_unit(cfg):
    return cfg.getint("autodetect", "modbus_deep_scan_max_unit")


def autodetect_rescan_seconds(cfg):
    return cfg.getfloat("autodetect", "rescan_seconds")


def autodetect_mstp_probe_timeout(cfg):
    return cfg.getfloat("autodetect", "mstp_probe_timeout_s")


def autodetect_modbus_probe_timeout(cfg):
    return cfg.getfloat("autodetect", "modbus_probe_timeout_s")


def csv_path(cfg):
    return os.path.expanduser(cfg["csv"]["path"])


def csv_desktop_copy_path(cfg):
    value = cfg["csv"].get("desktop_copy", "").strip()
    return os.path.expanduser(value) if value else None


def csv_write_seconds(cfg):
    return cfg.getfloat("csv", "write_seconds")
