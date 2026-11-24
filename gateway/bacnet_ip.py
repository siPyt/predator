"""BACnet/IP: predator's local virtual device, plus Who-Is/I-Am discovery.

Exposes points-map entries as BACnet objects (so any BACnet/IP client can
browse/read/write predator like a normal device) and forwards incoming writes
back to the real MS/TP object or Modbus register. Also runs a periodic global
Who-Is so any BACnet/IP device that shows up on the Ethernet port gets
recorded automatically - satisfying "plug in BACnet/IP and it finds it" the
same way gateway/discovery.py does for the serial side.

Uses bacpypes3 (asyncio-based), NOT classic bacpypes: classic bacpypes depends
on Python's `asyncore`/`asynchat` modules, which were removed in Python 3.12 -
confirmed by actually trying to import it during development, and Raspberry
Pi OS's current Python is 3.12+/Debian trixie's 3.13, so classic bacpypes
cannot run there at all. Every API call below (from_args address format,
who_is()'s return value, ObjectIdentifier-keyed object lookup, the
do_WritePropertyRequest override pattern, the vendor-id-999 default) was
verified live against the actual installed bacpypes3 package before writing
this, not assumed from memory - see sofar.txt Chapter 5 update.

Because bacpypes3 is asyncio-native, this whole module's entry point
(`async_main`) is a coroutine: gateway/service.py runs it via `asyncio.run()`
in its own dedicated thread, alongside the plain-thread discovery/API/CSV
loops.

SCOPE NOTE: this records discovered BACnet/IP devices (instance, address,
vendor, max-APDU) from Who-Is/I-Am, but does not (yet) walk each remote
device's full object list - that's a safe, separate follow-up once this is
confirmed running on the Pi.
"""

import asyncio
import logging
import time

from bacpypes3.argparse import SimpleArgumentParser
from bacpypes3.app import Application
from bacpypes3.local.analog import AnalogValueObjectCmd, AnalogValueObject
from bacpypes3.local.binary import BinaryValueObjectCmd, BinaryValueObject
from bacpypes3.pdu import Address
from bacpypes3.primitivedata import Enumerated, Null, ObjectIdentifier, Real, Unsigned
from bacpypes3.vendor import VendorInfo, get_vendor_info

from .runtime_state import STATE
from .points import parse_object

log = logging.getLogger("predator.bacnet_ip")

# ASHRAE reserves 999 for exactly this - a custom/unregistered device that
# doesn't need (and shouldn't claim) another company's real vendor id.
DEFAULT_VENDOR_ID = 999

_WRITABLE_CLASSES = {"analog-value": AnalogValueObjectCmd, "binary-value": BinaryValueObjectCmd}
_READONLY_CLASSES = {"analog-value": AnalogValueObject, "binary-value": BinaryValueObject}
_WRITABLE_OBJECT_TYPES = {
    "analog-output", "analog-value", "binary-output", "binary-value",
    "multi-state-output", "multi-state-value",
}
_VALUE_OBJECT_TYPES = {
    "analog-input", "analog-output", "analog-value",
    "binary-input", "binary-output", "binary-value",
    "multi-state-input", "multi-state-output", "multi-state-value",
}
_ANALOG_OBJECT_TYPES = {"analog-input", "analog-output", "analog-value"}

_RUNTIME = {"app": None, "loop": None}


def _oid(obj_type, obj_inst):
    return ObjectIdentifier((obj_type, obj_inst))


def _norm_type(obj_type):
    """Normalize bacpypes3 camelCase or hyphenated object type -> 'analog-input' style."""
    s = str(obj_type or "").strip()
    if "-" in s:
        return s.lower()
    out = []
    for i, ch in enumerate(s):
        if ch.isupper() and i > 0:
            out.append("-")
        out.append(ch.lower())
    return "".join(out)


def _clean_prop_val(val):
    if val is None:
        return ""
    if isinstance(val, (int, float)):
        return round(val, 4) if isinstance(val, float) else val
    s = str(val).strip()
    if s.lower() in ("no-units", "nounits", "null", "none"):
        return ""
    return s


def build_application(cfg):
    s = cfg["bacnet_ip"]
    vendor_id = int(s["vendor_id"])
    if vendor_id != DEFAULT_VENDOR_ID:
        try:
            get_vendor_info(vendor_id)
        except Exception:  # noqa: BLE001
            VendorInfo(vendor_id)

    bind = s["bind"] or "0.0.0.0"
    argv = [
        "--name", s["device_name"],
        "--instance", s["device_instance"],
        "--address", f"{bind}/24:{s['port']}",
    ]
    if vendor_id != DEFAULT_VENDOR_ID:
        argv += ["--vendoridentifier", str(vendor_id)]
    args = SimpleArgumentParser().parse_args(argv)
    return Application.from_args(args)


def install_write_forwarding(app, write_callback):
    """Monkey-patch the instance (bacpypes3's Application.from_args() returns
    a plain Application, not a subclass we control) so predator still gets to
    forward commanded writes to the real MS/TP object / Modbus register after
    bacpypes3 applies them locally. write_callback does blocking I/O (a
    subprocess call for MS/TP, serial I/O for Modbus), so it's dispatched via
    run_in_executor rather than awaited directly - running it inline here
    would stall this whole asyncio event loop (and every other BACnet/IP
    request) for however long that I/O takes."""
    original = app.do_WritePropertyRequest

    async def _patched(apdu):
        await original(apdu)
        try:
            obj_type, obj_inst = apdu.objectIdentifier
            obj = app.get_object_id(_oid(obj_type, obj_inst))
            if obj is None:
                return
            value = obj.presentValue
            if isinstance(value, (int, float)):
                value = float(value)
            else:
                value = bool(value)
            loop = asyncio.get_running_loop()
            loop.run_in_executor(None, write_callback, obj_type, obj_inst, value, apdu.priority)
        except Exception:  # noqa: BLE001
            log.exception("error forwarding BACnet/IP write")

    app.do_WritePropertyRequest = _patched


def add_point_object(app, point):
    """Expose one points-map entry as a local BACnet object."""
    obj_type, obj_inst = parse_object(point["exposed_object"])
    classes = _WRITABLE_CLASSES if point.get("writable") else _READONLY_CLASSES
    cls = classes.get(obj_type)
    if cls is None:
        raise ValueError(f"unsupported exposed_object type {obj_type}")
    obj = cls(objectIdentifier=(obj_type, obj_inst), objectName=point["name"], presentValue=Real(0.0))
    app.add_object(obj)
    return obj


def update_point_value(app, point, value):
    obj_type, obj_inst = parse_object(point["exposed_object"])
    obj = app.get_object_id(_oid(obj_type, obj_inst))
    if obj is None:
        return
    obj.presentValue = Real(float(value)) if isinstance(value, (int, float)) and not isinstance(value, bool) else bool(value)


async def _safe_read_prop(app, addr, oid, prop, array_index=None, timeout=2.5):
    try:
        coro = app.read_property(addr, oid, prop, array_index=array_index)
        return await asyncio.wait_for(coro, timeout=timeout)
    except Exception:  # noqa: BLE001
        return None


async def walk_remote_device(app, address_str, device_instance, max_objects=200):
    """Read device name + object list + per-object name/present-value/units/description."""
    addr = Address(address_str)
    dev_oid = _oid("device", int(device_instance))
    dev_name = await _safe_read_prop(app, addr, dev_oid, "objectName")
    dev_name_str = _clean_prop_val(dev_name)

    obj_list = await _safe_read_prop(app, addr, dev_oid, "objectList", timeout=4.0)
    oids = []
    if obj_list is not None:
        try:
            oids = list(obj_list)[:max_objects]
        except TypeError:
            oids = []
    if not oids:
        count = await _safe_read_prop(app, addr, dev_oid, "objectList", array_index=0)
        if isinstance(count, int) and count > 0:
            for idx in range(1, min(count, max_objects) + 1):
                item = await _safe_read_prop(app, addr, dev_oid, "objectList", array_index=idx)
                if item is not None:
                    oids.append(item)

    objects = []
    for item in oids:
        try:
            raw_type, raw_inst = item
        except (TypeError, ValueError):
            continue
        otype = _norm_type(raw_type)
        oinst = int(raw_inst)
        if otype == "device":
            continue
        oid = _oid(otype, oinst)
        name = _clean_prop_val(await _safe_read_prop(app, addr, oid, "objectName"))
        val = ""
        units = ""
        desc = _clean_prop_val(await _safe_read_prop(app, addr, oid, "description"))
        if otype in _VALUE_OBJECT_TYPES:
            val = _clean_prop_val(await _safe_read_prop(app, addr, oid, "presentValue"))
            if otype in _ANALOG_OBJECT_TYPES:
                units = _clean_prop_val(await _safe_read_prop(app, addr, oid, "units"))
        objects.append({
            "type": otype,
            "instance": oinst,
            "name": name or f"{otype}:{oinst}",
            "value": val,
            "units": units,
            "description": desc,
            "writable": otype in _WRITABLE_OBJECT_TYPES,
        })
    return dev_name_str, objects


async def read_remote_property(app, address_str, obj_type, obj_inst, prop="presentValue", timeout=4.0):
    addr = Address(address_str)
    oid = _oid(_norm_type(obj_type), int(obj_inst))
    val = await asyncio.wait_for(app.read_property(addr, oid, prop), timeout=timeout)
    return _clean_prop_val(val)


def _coerce_bacnet_write_value(obj_type, value):
    if value is None or (isinstance(value, str) and value.strip().lower() in ("null", "none", "relinquish", "")):
        return Null(())
    ot = _norm_type(obj_type)
    if ot in ("binary-output", "binary-value", "binary-input"):
        if isinstance(value, str):
            v = 1 if value.strip().lower() in ("1", "true", "active", "on", "yes") else 0
        else:
            v = 1 if bool(value) else 0
        return Enumerated(v)
    if ot in ("multi-state-output", "multi-state-value", "multi-state-input"):
        return Unsigned(int(float(value)))
    return Real(float(value))


async def write_remote_property(app, address_str, obj_type, obj_inst, value, priority=8, prop="presentValue", timeout=4.0):
    addr = Address(address_str)
    oid = _oid(_norm_type(obj_type), int(obj_inst))
    coerced = _coerce_bacnet_write_value(obj_type, value)
    await asyncio.wait_for(
        app.write_property(addr, oid, prop, coerced, priority=int(priority)),
        timeout=timeout,
    )
    return True


def live_read_property(address_str, obj_type, obj_inst, prop="presentValue", timeout=5.0):
    """Thread-safe sync helper for gateway/api.py to read any BACnet/IP object live."""
    app, loop = _RUNTIME.get("app"), _RUNTIME.get("loop")
    if app is None or loop is None:
        raise RuntimeError("BACnet/IP service is not ready")
    fut = asyncio.run_coroutine_threadsafe(
        read_remote_property(app, address_str, obj_type, obj_inst, prop=prop, timeout=timeout),
        loop,
    )
    return fut.result(timeout=timeout + 2.0)


def live_write_property(address_str, obj_type, obj_inst, value, priority=8, prop="presentValue", timeout=5.0):
    """Thread-safe sync helper for gateway/api.py to write (or relinquish) any BACnet/IP object."""
    app, loop = _RUNTIME.get("app"), _RUNTIME.get("loop")
    if app is None or loop is None:
        raise RuntimeError("BACnet/IP service is not ready")
    fut = asyncio.run_coroutine_threadsafe(
        write_remote_property(app, address_str, obj_type, obj_inst, value, priority=priority, prop=prop, timeout=timeout),
        loop,
    )
    return fut.result(timeout=timeout + 2.0)


async def discovery_whois_loop(app, cfg, stop_event):
    """Periodic global Who-Is for BACnet/IP discovery + object-list enumeration."""
    interval = cfg.getfloat("bacnet_ip", "discovery_whois_seconds")
    walk_enabled = cfg.getboolean("bacnet_ip", "walk_objects", fallback=True)
    max_objects = cfg.getint("bacnet_ip", "max_objects_per_device", fallback=200)
    refresh_sec = cfg.getfloat("bacnet_ip", "object_refresh_seconds", fallback=300.0)
    our_instance = cfg.getint("bacnet_ip", "device_instance")
    last_walked = {}

    while not stop_event.is_set():
        try:
            responses = await app.who_is(timeout=min(interval, 10))
            for apdu in responses:
                inst = int(apdu.iAmDeviceIdentifier[1])
                if inst == our_instance:
                    continue
                addr_str = str(getattr(apdu, "pduSource", ""))
                STATE.upsert_bacnet_ip_device(
                    device_instance=inst,
                    address=addr_str,
                    max_apdu=apdu.maxAPDULengthAccepted,
                    segmentation=str(apdu.segmentationSupported),
                    vendor_id=apdu.vendorID,
                )
                now = time.time()
                if walk_enabled and addr_str and (now - last_walked.get(inst, 0) >= refresh_sec):
                    try:
                        dev_name, objects = await walk_remote_device(app, addr_str, inst, max_objects=max_objects)
                        STATE.upsert_bacnet_ip_device(
                            device_instance=inst,
                            address=addr_str,
                            device_name=dev_name,
                            objects=objects,
                        )
                        last_walked[inst] = now
                        log.info("BACnet/IP device %s (%s): walked %d object(s)", inst, addr_str, len(objects))
                    except Exception:  # noqa: BLE001
                        log.exception("error walking objects on BACnet/IP device %s", inst)
            if responses:
                log.info("Who-Is found %d device(s)", len(responses))
        except Exception:  # noqa: BLE001
            log.exception("error during discovery Who-Is")
        await asyncio.sleep(max(interval - 10, 1))


async def async_main(cfg, write_callback, points, stop_event, on_ready=None):
    """gateway/service.py's bacnet-ip thread target runs this via
    asyncio.run()."""
    app = build_application(cfg)
    install_write_forwarding(app, write_callback)
    for p in points:
        try:
            add_point_object(app, p)
        except Exception:  # noqa: BLE001
            log.exception("error exposing point %s on BACnet/IP", p["name"])

    loop = asyncio.get_running_loop()
    _RUNTIME["app"] = app
    _RUNTIME["loop"] = loop
    if on_ready is not None:
        on_ready(app, loop)

    log.info("BACnet/IP virtual device running (instance %s)", cfg["bacnet_ip"]["device_instance"])
    discovery_task = asyncio.ensure_future(discovery_whois_loop(app, cfg, stop_event))
    try:
        while not stop_event.is_set():
            await asyncio.sleep(0.5)
    finally:
        discovery_task.cancel()
        _RUNTIME["app"] = None
        _RUNTIME["loop"] = None
        app.close()


def run_in_thread(cfg, write_callback, points, stop_event, on_ready=None):
    """Thread target: bacpypes3 needs a running asyncio loop, so this is the
    one piece of the service that gets `asyncio.run()` instead of a plain
    blocking loop."""
    asyncio.run(async_main(cfg, write_callback, points, stop_event, on_ready))
