"""Siemens APOGEE P1 (FLN) RS-485 capture.

The P1 message layout is Siemens-proprietary and is not published, so this scanner does NOT guess at
field meanings: it listens (never transmits) at the usual P1 rates and writes every received line to
the CSV as printable text plus hex. Once a Siemens P1 spec or a capture from the real bus is
available, add a decoder in `decode_line`.
"""
import re
import time

from ..context import Context, SerialSpec
from ..records import Record
from ..serialutil import open_serial

BAUDS = [4800, 9600, 1200, 2400, 19200]
SEPARATORS = re.compile(rb"[\r\n\x03\x04]+")


def decode_line(raw):
    """Hook for a real P1 decoder; currently only the printable-text rendering."""
    text = "".join(chr(b) if 32 <= b < 127 else "." for b in raw)
    return text, {}


def scan_serial(ctx: Context, spec: SerialSpec):
    best = None
    for baud in spec.bauds or BAUDS:
        ctx.log("siemens-p1 %s %d baud (passive, %.0fs)" % (spec.port, baud, min(ctx.sniff_seconds, 10)))
        ser = open_serial(spec.port, baud, spec.parity or "N", 0.1)
        buf, lines = b"", []
        deadline = time.monotonic() + min(ctx.sniff_seconds, 10)
        try:
            while time.monotonic() < deadline:
                buf += ser.read(512)
                *done, buf = SEPARATORS.split(buf)
                lines.extend(done)
        finally:
            ser.close()
        lines = [l for l in lines if l]
        printable = sum(1 for l in lines if all(32 <= b < 127 for b in l))
        if lines and (best is None or printable > best[1]):
            best = (baud, printable, lines)
        if lines and printable >= len(lines) * 0.8:
            break
    if not best:
        return
    baud, _score, lines = best
    for raw in lines:
        text, extra = decode_line(raw)
        ctx.emit(Record("Siemens P1", spec.port, "", "raw-frame", text,
                        details=dict(extra, baud=baud, hex=raw.hex(), decoded=False)))
