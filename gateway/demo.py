"""Realistic HVAC/chiller demo dataset so users can preview every Predator
screen (Points, Mirius/ICC, Serial Ports, Bus Doctor, Recon, Snapshots/Diff)
without needing live field equipment plugged in.
"""

import time

from . import snapshots
from .runtime_state import MODBUS_RTU, MSTP, STATE

DEMO_SNAPSHOT_ID = "Demo_Chiller_Before"

DEMO_MSTP_OBJECTS = [
    {"type": "analog-input", "instance": 1, "name": "EntEvapWaterTemp", "value": 54.2, "units": "degrees-fahrenheit", "description": "Entering Chilled Water Temp", "writable": False},
    {"type": "analog-input", "instance": 2, "name": "LvgEvapWaterTemp", "value": 44.6, "units": "degrees-fahrenheit", "description": "Leaving Chilled Water Temp", "writable": False},
    {"type": "analog-input", "instance": 3, "name": "CondRefPressure", "value": 142.8, "units": "pounds-force-per-square-inch", "description": "Condenser Refrigerant Pressure", "writable": False},
    {"type": "analog-value", "instance": 1, "name": "ChilledWaterSP", "value": 44.0, "units": "degrees-fahrenheit", "description": "Active Chilled Water Setpoint", "writable": True},
    {"type": "analog-value", "instance": 2, "name": "DemandLimitPct", "value": 85.0, "units": "percent", "description": "Compressor Demand Limit", "writable": True},
    {"type": "binary-input", "instance": 1, "name": "EvapFlowStatus", "value": "active", "units": "", "description": "Evaporator Flow Switch", "writable": False},
    {"type": "binary-output", "instance": 1, "name": "ChillerEnable", "value": "active", "units": "", "description": "Chiller Run Command", "writable": True},
    {"type": "multi-state-value", "instance": 1, "name": "OperatingMode", "value": 2, "units": "", "description": "1=Off 2=Cool 3=Ice", "writable": True},
]

DEMO_BACNET_IP_OBJECTS = [
    {"type": "analog-input", "instance": 1, "name": "SupplyAirTemp", "value": 55.4, "units": "degrees-fahrenheit", "description": "AHU-01 Supply Air Temp", "writable": False},
    {"type": "analog-value", "instance": 1, "name": "StaticPressSP", "value": 1.25, "units": "inches-of-water", "description": "Duct Static Setpoint", "writable": True},
    {"type": "binary-input", "instance": 1, "name": "SupplyFanStatus", "value": "active", "units": "", "description": "Supply Fan Proof", "writable": False},
]

DEMO_BUS_DOCTOR_REPORT = {
    "port": "/dev/ttyUSB0",
    "baud": 19200,
    "duration_s": 5.0,
    "total_bytes": 1842,
    "valid_frames": 186,
    "preambles": 186,
    "header_crc_errors": 0,
    "data_crc_errors": 0,
    "tokens_per_sec": 34.2,
    "active_masters": [
        {"mac": 1, "hex": "0x01", "frames": 94},
        {"mac": 100, "hex": "0x64", "frames": 92},
    ],
    "frame_counts": {"token": 171, "poll-for-master": 9, "data-expecting-reply": 4, "data-not-expecting-reply": 2},
    "token_ring": {"1->100": 86, "100->1": 85},
    "iam_devices": {"9050": {"mac": 1, "vendor_id": 2}},
    "passive_points": [
        {"mac": 1, "object": "analog-input:1", "property": "present-value", "value": 54.2},
        {"mac": 1, "object": "analog-input:2", "property": "present-value", "value": 44.6},
        {"mac": 1, "object": "analog-value:1", "property": "present-value", "value": 44.0},
    ],
    "health": "healthy",
    "verdicts": [
        "[DEMO] Healthy MS/TP trunk at 19200 baud: 2 active master(s) (MAC 1, MAC 100), 34.2 tokens/sec, 0 CRC errors."
    ],
}

DEMO_RECON_RECORDS = [
    {"protocol": "BACnet", "source": "192.168.1.25", "device": "3200", "point": "device", "value": "AHU-01", "units": "", "details": {}},
    {"protocol": "BACnet", "source": "192.168.1.25", "device": "3200", "point": "analog-input:1", "value": 55.4, "units": "degF", "details": {}},
    {"protocol": "Modbus TCP", "source": "192.168.1.40:502", "device": "1", "point": "holding:0", "value": 1785, "units": "", "details": {}},
    {"protocol": "PROFINET", "source": "192.168.1.60", "device": "plc-chiller-1", "point": "identity", "value": "S7-1500", "units": "", "details": {}},
    {"protocol": "S7", "source": "192.168.1.60", "device": "cpu", "point": "order-number", "value": "6ES7 512-1DK01-0AB0", "units": "", "details": {}},
    {"protocol": "MQTT", "source": "192.168.1.20:1883", "device": "plant/chiller1", "point": "kw", "value": 184.5, "units": "kW", "details": {}},
]

DEMO_RECON_LOGS = [
    "[DEMO] Subnets: 192.168.1.0/24",
    "[DEMO] Running bacnet... found 1 device (3200 @ 192.168.1.25)",
    "[DEMO] Running modbus... found 1 server (192.168.1.40:502)",
    "[DEMO] Running profinet... found plc-chiller-1 (192.168.1.60)",
    "[DEMO] Running s7... read SZL 0x0011 order number",
    "[DEMO] Running mqtt... subscribed # on 192.168.1.20:1883",
    "[DEMO] Complete: 6 sample record(s)",
]


def load_demo(cfg, bd_state=None, bd_lock=None, recon_mgr=None):
    """Populate live runtime state, Bus Doctor, Recon, and a comparison Snapshot with demo data."""
    now = time.time()
    STATE.set_port_state(
        "/dev/ttyUSB0",
        protocol=MSTP,
        baud=19200,
        parity="N",
        devices=[9050],
        device_macs={9050: 1},
        device_objects={9050: [dict(o) for o in DEMO_MSTP_OBJECTS]},
        last_scan=now,
        last_confirmed=now,
        note="[DEMO] 1 MS/TP device (MAC: 9050=0x01/1)",
    )
    STATE.set_port_state(
        "/dev/ttyUSB1",
        protocol=MODBUS_RTU,
        baud=9600,
        parity="N",
        devices=[10],
        device_macs={},
        device_objects={},
        last_scan=now,
        last_confirmed=now,
        note="[DEMO] 1 Modbus RTU unit (VFD-10)",
    )
    STATE.upsert_bacnet_ip_device(
        device_instance=3200,
        address="192.168.1.25",
        max_apdu=1476,
        segmentation="segmented-both",
        vendor_id=7,
        device_name="[DEMO] AHU-01",
        objects=[dict(o) for o in DEMO_BACNET_IP_OBJECTS],
    )

    if bd_state is not None and bd_lock is not None:
        with bd_lock:
            bd_state.update({
                "running": False,
                "port": "/dev/ttyUSB0",
                "report": dict(DEMO_BUS_DOCTOR_REPORT),
                "error": None,
                "ts": now,
            })

    if recon_mgr is not None:
        with recon_mgr._lock:
            recon_mgr._running = False
            recon_mgr._current = ""
            recon_mgr._subnets = ["192.168.1.0/24"]
            recon_mgr._protocols = ["bacnet", "modbus", "profinet", "s7", "mqtt"]
            recon_mgr._logs = list(DEMO_RECON_LOGS)
            recon_mgr._records = [dict(r) for r in DEMO_RECON_RECORDS]
            recon_mgr._error = None

    # Save a 'before' snapshot with slight differences so Diff immediately demonstrates + / - / ~
    before_rows = [
        {"source": "mstp", "bus_or_network": "/dev/ttyUSB0@19200", "address": 9050,
         "object_or_register": "analog-input:1", "description": "EntEvapWaterTemp",
         "value": 52.0, "units": "degrees-fahrenheit", "writable": False},
        {"source": "mstp", "bus_or_network": "/dev/ttyUSB0@19200", "address": 9050,
         "object_or_register": "analog-input:2", "description": "LvgEvapWaterTemp",
         "value": 44.6, "units": "degrees-fahrenheit", "writable": False},
        {"source": "mstp", "bus_or_network": "/dev/ttyUSB0@19200", "address": 9050,
         "object_or_register": "analog-value:1", "description": "ChilledWaterSP",
         "value": 45.0, "units": "degrees-fahrenheit", "writable": True},
        {"source": "mstp", "bus_or_network": "/dev/ttyUSB0@19200", "address": 9050,
         "object_or_register": "binary-input:99", "description": "OldAlarmPoint",
         "value": "inactive", "units": "", "writable": False},
    ]
    snapshots.save_snapshot(cfg, DEMO_SNAPSHOT_ID, before_rows, meta={"demo": True})
    return {"demo": True, "mstp_points": len(DEMO_MSTP_OBJECTS), "bacnet_ip_points": len(DEMO_BACNET_IP_OBJECTS)}


def clear_demo(cfg, bd_state=None, bd_lock=None, recon_mgr=None):
    """Remove simulated demo state so Predator returns to live-only mode."""
    for p in ("/dev/ttyUSB0", "/dev/ttyUSB1"):
        st = STATE.get_port_state(p)
        if st and "[DEMO]" in (st.note or ""):
            STATE.remove_port(p)
    with STATE._lock:
        dev = STATE._bacnet_ip.get(3200)
        if dev and "[DEMO]" in (dev.device_name or ""):
            STATE._bacnet_ip.pop(3200, None)
    if bd_state is not None and bd_lock is not None:
        with bd_lock:
            rep = bd_state.get("report") or {}
            if any("[DEMO]" in v for v in (rep.get("verdicts") or [])):
                bd_state.update({"running": False, "port": None, "report": None, "error": None, "ts": 0.0})
    if recon_mgr is not None:
        with recon_mgr._lock:
            if any("[DEMO]" in ln for ln in recon_mgr._logs):
                recon_mgr._logs = []
                recon_mgr._records = []
    snapshots.delete_snapshot(cfg, DEMO_SNAPSHOT_ID)
    return {"demo": False}
