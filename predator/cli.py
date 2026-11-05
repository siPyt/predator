import argparse
import os
import sys
import traceback

from . import __version__
from .context import Context, SerialSpec, detect_subnets, parse_units
from .records import CsvSink
from .scanners import IP_SCANNERS, SERIAL_SCANNERS


def parse_serial(text):
    """PORT:PROTOCOL[:BAUD[,BAUD]][:PARITY]  e.g. /dev/ttyAMA2:modbus-rtu:9600,19200:E"""
    parts = text.split(":")
    if len(parts) < 2 or parts[1] not in SERIAL_SCANNERS:
        raise argparse.ArgumentTypeError("use PORT:PROTOCOL[:BAUDS][:PARITY]; protocols: %s" % ", ".join(SERIAL_SCANNERS))
    bauds = [int(b) for b in parts[2].split(",") if b] if len(parts) > 2 else []
    parity = parts[3].upper() if len(parts) > 3 else ""
    if parity not in ("", "N", "E", "O"):
        raise argparse.ArgumentTypeError("parity must be N, E or O")
    return SerialSpec(parts[0], parts[1], bauds, parity)


def parse_broker(text):
    host, _, port = text.partition(":")
    return host, int(port or 1883)


def build_parser():
    p = argparse.ArgumentParser(prog="predator", description="Scan field buses and write everything found to CSV.")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("-o", "--out", default="predator_scan.csv", help="CSV output file")
    p.add_argument("--protocols", default="all",
                   help="comma list of: %s (default: all)" % ", ".join(IP_SCANNERS))
    p.add_argument("--subnet", action="append", default=[], help="CIDR to scan (repeatable; default: local subnets)")
    p.add_argument("--iface", help="interface for layer-2 scans such as PROFINET DCP (default: all ethernet)")
    p.add_argument("--serial", action="append", default=[], type=parse_serial, metavar="PORT:PROTOCOL[:BAUDS][:PARITY]",
                   help="serial scan (repeatable); protocols: %s" % ", ".join(SERIAL_SCANNERS))
    p.add_argument("--mqtt-broker", action="append", default=[], type=parse_broker, metavar="HOST[:PORT]")
    p.add_argument("--mqtt-user")
    p.add_argument("--mqtt-seconds", type=float, default=20.0)
    p.add_argument("--modbus-units", default="1-10", help="unit ids, e.g. 1-10,247")
    p.add_argument("--modbus-registers", type=int, default=32, help="registers/coils per table to read")
    p.add_argument("--max-points", type=int, default=500, help="max BACnet objects read per device")
    p.add_argument("--rs485-rts", action="store_true", help="drive RTS for RS-485 direction (Modbus RTU on UART HATs)")
    p.add_argument("--timeout", type=float, default=3.0)
    p.add_argument("--sniff-seconds", type=float, default=20.0, help="passive listen time per baud on serial buses")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    sink = CsvSink(args.out)
    ctx = Context(
        emit=sink.write,
        subnets=args.subnet or detect_subnets(),
        iface=args.iface,
        timeout=args.timeout,
        sniff_seconds=args.sniff_seconds,
        max_points=args.max_points,
        modbus_units=parse_units(args.modbus_units),
        modbus_registers=args.modbus_registers,
        mqtt_brokers=args.mqtt_broker,
        mqtt_user=args.mqtt_user,
        mqtt_password=os.environ.get("PREDATOR_MQTT_PASSWORD"),
        mqtt_seconds=args.mqtt_seconds,
        serial=args.serial,
        rs485=args.rs485_rts,
    )
    wanted = list(IP_SCANNERS) if args.protocols == "all" else [n.strip() for n in args.protocols.split(",")]
    unknown = [n for n in wanted if n not in IP_SCANNERS]
    if unknown:
        print("unknown protocol(s): %s" % ", ".join(unknown), file=sys.stderr)
        return 2
    if not ctx.subnets and not ctx.mqtt_brokers:
        print("no subnet detected; pass --subnet CIDR", file=sys.stderr)
    print("subnets: %s" % (", ".join(ctx.subnets) or "none"), file=sys.stderr)

    jobs = [(name, lambda n=name: IP_SCANNERS[n](ctx)) for name in wanted]
    jobs += [("%s@%s" % (s.protocol, s.port), lambda s=s: SERIAL_SCANNERS[s.protocol](ctx, s)) for s in ctx.serial]
    for name, job in jobs:
        print("== %s" % name, file=sys.stderr)
        try:
            job()
        except (OSError, ValueError) as exc:
            print("%s failed: %s" % (name, exc), file=sys.stderr)
        except Exception:
            traceback.print_exc()
    sink.close()
    print("%d rows -> %s" % (sink.count, args.out), file=sys.stderr)
    return 0
