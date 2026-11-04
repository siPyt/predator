"""Configuration loading for the predator gateway.

Reads one INI file (default: ~/predator_gateway.conf) with sane defaults, so the
service can start even before the file exists. Serial device paths default to
USB-to-RS485 adapters (/dev/ttyUSB*), not the Pi's GPIO UARTs.
"""

import configparser
import os

DEFAULT_CONFIG_PATH = os.path.expanduser(
    os.environ.get("PREDATOR_GATEWAY_CONFIG", "~/predator_gateway.conf")
)

_DEFAULTS = {
    "mstp": {
        # BACnet MS/TP trunk, read via the bacnet-stack CLI tools (bacrp/bacwp/bacwi/bacepics).
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
        "device_instance": "599100",
        "device_name": "PredatorGateway",
        "vendor_id": "15",
        "bind": "",
        "port": "47808",
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
