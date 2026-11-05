"""PROFIBUS DP/FMS passive bus monitor (8E1 UART via an RS-485 adapter; listen-only, never transmits)."""
import time

from ..context import Context, SerialSpec
from ..records import Record
from ..serialutil import open_serial

BAUDS = [9600, 19200, 45450, 93750, 187500, 500000, 1500000]


def parse_telegrams(buf):
    """Returns ([(sd, da, sa, fc, du)], remaining bytes); only checksum-valid telegrams are returned."""
    out, i = [], 0
    while i < len(buf):
        sd = buf[i]
        if sd == 0xE5:
            i += 1
        elif sd == 0x10:
            if i + 6 > len(buf):
                break
            t = buf[i : i + 6]
            if t[5] == 0x16 and (t[1] + t[2] + t[3]) & 0xFF == t[4]:
                out.append((sd, t[1], t[2], t[3], b""))
                i += 6
            else:
                i += 1
        elif sd == 0x68:
            if i + 4 > len(buf):
                break
            le = buf[i + 1]
            if buf[i + 2] != le or buf[i + 3] != 0x68 or le < 3:
                i += 1
                continue
            if i + le + 6 > len(buf):
                break
            t = buf[i : i + le + 6]
            if t[-1] == 0x16 and sum(t[4:-2]) & 0xFF == t[-2]:
                out.append((sd, t[4], t[5], t[6], bytes(t[7:-2])))
                i += le + 6
            else:
                i += 1
        elif sd == 0xA2:
            if i + 14 > len(buf):
                break
            t = buf[i : i + 14]
            if t[-1] == 0x16 and sum(t[1:-2]) & 0xFF == t[-2]:
                out.append((sd, t[1], t[2], t[3], bytes(t[4:-2])))
                i += 14
            else:
                i += 1
        elif sd == 0xDC:
            if i + 3 > len(buf):
                break
            out.append((sd, buf[i + 1], buf[i + 2], None, b""))
            i += 3
        else:
            i += 1
    return out, buf[i:]


def scan_serial(ctx: Context, spec: SerialSpec):
    for baud in spec.bauds or BAUDS:
        ctx.log("profibus %s %d baud 8E1 (passive, %.0fs)" % (spec.port, baud, min(ctx.sniff_seconds, 10)))
        try:
            ser = open_serial(spec.port, baud, "E", 0.05)
        except (ValueError, OSError) as exc:
            ctx.log("profibus: cannot open %d baud: %s" % (baud, exc))
            continue
        stations, last_du, buf, tokens = {}, {}, b"", 0
        deadline = time.monotonic() + min(ctx.sniff_seconds, 10)
        try:
            while time.monotonic() < deadline:
                buf += ser.read(1024)
                telegrams, buf = parse_telegrams(buf)
                for sd, da, sa, fc, du in telegrams:
                    if sd == 0xDC or fc is None:
                        tokens += 1
                        stations.setdefault(sa & 0x7F, "master")
                        continue
                    if fc & 0x40:
                        stations.setdefault(sa & 0x7F, "master")
                        stations.setdefault(da & 0x7F, "slave")
                        if du:
                            last_du[(da & 0x7F, "out")] = du
                    elif du:
                        last_du[(sa & 0x7F, "in")] = du
        finally:
            ser.close()
        if stations:
            for addr, role in sorted(stations.items()):
                ctx.emit(Record("PROFIBUS", spec.port, str(addr), "station", role, details={"baud": baud}))
            for (addr, direction), du in sorted(last_du.items()):
                ctx.emit(Record("PROFIBUS", spec.port, str(addr), "process-data-%s" % direction, du.hex(),
                                details={"baud": baud, "note": "raw bytes; meaning needs the device GSD file"}))
            return
