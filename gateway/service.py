"""predator-gateway systemd service entrypoint.

Starts, in parallel:
  - gateway/discovery.py's serial auto-detect loop (thread)
  - gateway/bacnet_ip.py's virtual BACnet/IP device (its own thread running
    its own asyncio event loop via asyncio.run() - bacpypes3 is asyncio-native,
    classic bacpypes's blocking run() loop isn't used/doesn't even work on
    Python 3.12+, see gateway/bacnet_ip.py's module docstring) + periodic
    discovery Who-Is
  - the curated points-map poller (thread, this module) - reads real MS/TP
    objects / Modbus registers and republishes them as BACnet/IP present-values
    via loop.call_soon_threadsafe() (bacnet_ip's objects live on its own
    thread's event loop), forwarding incoming BACnet/IP writes back to the
    real point
  - gateway/csv_export.py's periodic CSV writer (thread)
  - gateway/api.py's status/download HTTP server (thread)

Ctrl-C / systemd stop sets one shared stop_event and everything unwinds.
"""

import logging
import signal
import threading
import time

from . import api
from . import bacnet_ip
from . import config as cfgmod
from . import csv_export
from . import discovery
from . import modbus_rtu
from . import mstp
from . import runtime_state
from .points import load_points, parse_object
from .runtime_state import STATE, MSTP, MODBUS_RTU

log = logging.getLogger("predator.service")


def _sync_cfg_from_runtime_state(cfg):
    """Point the existing mstp.py/modbus_rtu.py calls (which read iface/baud
    straight from cfg) at whatever gateway/discovery.py has actually found,
    so auto-detect doesn't require threading overrides through every call."""
    if not cfgmod.autodetect_enabled(cfg):
        return
    mstp_state = STATE.port_for_protocol(MSTP)
    if mstp_state and mstp_state.baud:
        cfg["mstp"]["iface"] = mstp_state.port
        cfg["mstp"]["baud"] = str(mstp_state.baud)
    modbus_state = STATE.port_for_protocol(MODBUS_RTU)
    if modbus_state and modbus_state.baud:
        cfg["modbus"]["iface"] = modbus_state.port
        cfg["modbus"]["baud"] = str(modbus_state.baud)


def _make_write_callback(cfg, points_by_object):
    def _write(obj_type, obj_inst, value, priority):
        point = points_by_object.get((obj_type, obj_inst))
        if point is None or not point.get("writable"):
            log.warning("ignoring write to unmapped/read-only object %s:%s", obj_type, obj_inst)
            return
        try:
            if point["source"] == "mstp":
                m = point["mstp"]
                mobj_type, mobj_inst = parse_object(m["object"])
                mstp.write_property(cfg, m["device_instance"], mobj_type, mobj_inst, value, priority=priority)
            elif point["source"] == "modbus":
                modbus_rtu.write_point(cfg, point["modbus"], value)
            log.info("forwarded write: %s = %s (priority %s)", point["name"], value, priority)
        except Exception:  # noqa: BLE001
            log.exception("error forwarding write for %s", point["name"])
    return _write


def _poll_points_loop(cfg, bacnet_handle, stop_event):
    last_poll = {}
    while not stop_event.is_set():
        _sync_cfg_from_runtime_state(cfg)
        try:
            points = load_points(cfgmod.points_map_path(cfg))
        except Exception:  # noqa: BLE001
            log.exception("error loading points map")
            stop_event.wait(5)
            continue

        now = time.time()
        for p in points:
            if p["source"] == "static":
                continue
            if now - last_poll.get(p["name"], 0) < p.get("poll_seconds", 5):
                continue
            last_poll[p["name"]] = now
            try:
                if p["source"] == "mstp":
                    m = p["mstp"]
                    obj_type, obj_inst = parse_object(m["object"])
                    value = mstp.read_property(cfg, m["device_instance"], obj_type, obj_inst)
                elif p["source"] == "modbus":
                    value = modbus_rtu.read_point(cfg, p["modbus"])
                else:
                    continue
                STATE.set_point_value(p["name"], value=value)
                # bacnet_ip's app/objects live on the asyncio event loop's own
                # thread (see gateway/bacnet_ip.py) - call_soon_threadsafe is
                # the correct way to touch them from this (different) thread.
                if bacnet_handle.get("loop") is not None:
                    bacnet_handle["loop"].call_soon_threadsafe(
                        bacnet_ip.update_point_value, bacnet_handle["app"], p, value,
                    )
            except Exception as e:  # noqa: BLE001
                STATE.set_point_value(p["name"], error=str(e))

        stop_event.wait(1)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    cfg = cfgmod.load_config()
    runtime_state.load_overrides(cfgmod.overrides_path(cfg))
    stop_event = threading.Event()
    threads = []

    try:
        points = load_points(cfgmod.points_map_path(cfg))
    except Exception:  # noqa: BLE001
        log.exception("error loading points map at startup - continuing with none")
        points = []
    points_by_object = {parse_object(p["exposed_object"]): p for p in points}

    bacnet_handle = {"app": None, "loop": None}
    bacnet_ready = threading.Event()

    def _on_bacnet_ready(app, loop):
        bacnet_handle["app"] = app
        bacnet_handle["loop"] = loop
        bacnet_ready.set()

    bacnet_thread = threading.Thread(
        target=bacnet_ip.run_in_thread,
        args=(cfg, _make_write_callback(cfg, points_by_object), points, stop_event, _on_bacnet_ready),
        name="bacnet-ip", daemon=True,
    )
    bacnet_thread.start()
    if not bacnet_ready.wait(timeout=10):
        log.warning("BACnet/IP virtual device did not report ready within 10s - continuing anyway")

    if cfgmod.autodetect_enabled(cfg):
        t = threading.Thread(target=discovery.run_loop, args=(cfg, stop_event), name="discovery", daemon=True)
        t.start()
        threads.append(t)
        log.info("serial auto-detect loop running")

    t = threading.Thread(target=_poll_points_loop, args=(cfg, bacnet_handle, stop_event), name="points-poller", daemon=True)
    t.start()
    threads.append(t)

    t = threading.Thread(target=csv_export.run_loop, args=(cfg, stop_event), name="csv-export", daemon=True)
    t.start()
    threads.append(t)

    api.start_server(cfg)

    def _handle_signal(signum, _frame):
        log.info("received signal %s, shutting down", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    log.info("predator-gateway running")
    while not stop_event.is_set():
        time.sleep(1)

    for t in threads:
        t.join(timeout=5)
    bacnet_thread.join(timeout=5)
    log.info("predator-gateway stopped")


if __name__ == "__main__":
    main()
