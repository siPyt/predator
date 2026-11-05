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

from bacpypes3.argparse import SimpleArgumentParser
from bacpypes3.app import Application
from bacpypes3.local.analog import AnalogValueObjectCmd, AnalogValueObject
from bacpypes3.local.binary import BinaryValueObjectCmd, BinaryValueObject
from bacpypes3.primitivedata import ObjectIdentifier, Real
from bacpypes3.vendor import VendorInfo, get_vendor_info

from .runtime_state import STATE
from .points import parse_object

log = logging.getLogger("predator.bacnet_ip")

# ASHRAE reserves 999 for exactly this - a custom/unregistered device that
# doesn't need (and shouldn't claim) another company's real vendor id.
# Any other id crashed bacpypes3's from_args() during testing (it tries to
# resolve vendor-specific object-class variants that don't exist unless the
# vendor id has full object/property class info registered) - confirmed live,
# so this is a verified workaround, not just caution.
DEFAULT_VENDOR_ID = 999

_WRITABLE_CLASSES = {"analog-value": AnalogValueObjectCmd, "binary-value": BinaryValueObjectCmd}
_READONLY_CLASSES = {"analog-value": AnalogValueObject, "binary-value": BinaryValueObject}


def _oid(obj_type, obj_inst):
    return ObjectIdentifier((obj_type, obj_inst))


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


async def discovery_whois_loop(app, cfg, stop_event):
    """Periodic global Who-Is for passive BACnet/IP discovery. who_is()
    returns the I-Am responses directly (verified: a plain list, no handler
    override needed), so this just records whatever comes back each round."""
    interval = cfg.getfloat("bacnet_ip", "discovery_whois_seconds")
    while not stop_event.is_set():
        try:
            responses = await app.who_is(timeout=min(interval, 10))
            for apdu in responses:
                STATE.upsert_bacnet_ip_device(
                    device_instance=apdu.iAmDeviceIdentifier[1],
                    address=str(getattr(apdu, "pduSource", "")),
                    max_apdu=apdu.maxAPDULengthAccepted,
                    segmentation=str(apdu.segmentationSupported),
                    vendor_id=apdu.vendorID,
                )
            if responses:
                log.info("Who-Is found %d device(s)", len(responses))
        except Exception:  # noqa: BLE001
            log.exception("error during discovery Who-Is")
        await asyncio.sleep(max(interval - 10, 1))


async def async_main(cfg, write_callback, points, stop_event, on_ready=None):
    """gateway/service.py's bacnet-ip thread target runs this via
    asyncio.run(). Builds the app, exposes the points map, runs discovery,
    and waits for stop_event (a plain threading.Event, polled - simplest way
    to bridge from the rest of the service's thread-based shutdown signal).

    `on_ready(app, loop)` hands the app and this thread's event loop back to
    gateway/service.py's points-poller thread, which needs both to safely
    update object values via loop.call_soon_threadsafe() instead of touching
    bacpypes3 objects directly from a different thread."""
    app = build_application(cfg)
    install_write_forwarding(app, write_callback)
    for p in points:
        try:
            add_point_object(app, p)
        except Exception:  # noqa: BLE001
            log.exception("error exposing point %s on BACnet/IP", p["name"])

    if on_ready is not None:
        on_ready(app, asyncio.get_running_loop())

    log.info("BACnet/IP virtual device running (instance %s)", cfg["bacnet_ip"]["device_instance"])
    discovery_task = asyncio.ensure_future(discovery_whois_loop(app, cfg, stop_event))
    try:
        while not stop_event.is_set():
            await asyncio.sleep(0.5)
    finally:
        discovery_task.cancel()
        app.close()


def run_in_thread(cfg, write_callback, points, stop_event, on_ready=None):
    """Thread target: bacpypes3 needs a running asyncio loop, so this is the
    one piece of the service that gets `asyncio.run()` instead of a plain
    blocking loop."""
    asyncio.run(async_main(cfg, write_callback, points, stop_event, on_ready))
