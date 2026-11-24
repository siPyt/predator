"""RS-485 Bus Doctor: passive MS/TP & framing diagnostics on any serial port.

Listens passively (never transmits!) using predator.scanners.bacnet's proven
MS/TP CRC-8/CRC-16 frame parser to measure:
  - Active master MACs & token-passing order
  - Token rotation rate (tokens/sec)
  - Frame breakdown (token, poll-for-master, data-expecting-reply, etc.)
  - Discovered I-Am devices + live passively observed point values
  - Byte/framing/CRC noise ratio and actionable wiring verdicts (no traffic,
    possible swapped A/B or wrong baud, duplicate MAC collision hints, healthy bus).
"""

import collections
import time

from predator.scanners.bacnet import (
    FRAME_TYPES,
    decode_apdu_points,
    mstp_data_crc,
    mstp_header_crc,
    parse_iam,
    parse_npdu,
)
from .runtime_state import STATE


def analyze_mstp_bytes(raw_bytes, elapsed_s=1.0, baud=0):
    """Pure-function analyzer for raw RS-485 capture bytes (easy to unit-test)."""
    buf = bytes(raw_bytes or b"")
    total_bytes = len(buf)
    preambles = 0
    header_crc_errors = 0
    data_crc_errors = 0
    frames = []
    i = 0
    while i + 8 <= len(buf):
        if buf[i] != 0x55 or buf[i + 1] != 0xFF:
            i += 1
            continue
        preambles += 1
        hdr = buf[i + 2 : i + 7]
        if mstp_header_crc(hdr) != buf[i + 7]:
            header_crc_errors += 1
            i += 2
            continue
        ftype, dest, src, ln = hdr[0], hdr[1], hdr[2], (hdr[3] << 8) | hdr[4]
        if ln == 0:
            frames.append((ftype, dest, src, b""))
            i += 8
            continue
        end = i + 8 + ln + 2
        if end > len(buf):
            break
        data = buf[i + 8 : i + 8 + ln]
        if mstp_data_crc(data) != (buf[end - 2] | (buf[end - 1] << 8)):
            data_crc_errors += 1
            i += 2
            continue
        frames.append((ftype, dest, src, data))
        i = end

    frame_counts = collections.Counter()
    masters = collections.Counter()
    token_edges = collections.Counter()
    devices = {}
    passive_points = []
    seen_pts = set()
    self_tokens = []

    for ftype, dest, src, data in frames:
        fname = FRAME_TYPES.get(ftype, f"type-{ftype}")
        frame_counts[fname] += 1
        if ftype in (0, 1, 2, 5, 6):
            masters[src] += 1
        if ftype == 0:
            token_edges[f"{src}->{dest}"] += 1
            if src == dest:
                self_tokens.append(src)
        if ftype in (5, 6) and data:
            parsed = parse_npdu(data)
            if parsed:
                apdu = data[parsed[1] :]
                iam = parse_iam(apdu)
                if iam and iam.get("object_type") == 8:
                    devices[iam["instance"]] = {"mac": src, "vendor_id": iam.get("vendor_id")}
                for otype, inst, prop, val in decode_apdu_points(apdu):
                    key = (src, otype, inst, prop)
                    if key not in seen_pts:
                        seen_pts.add(key)
                        passive_points.append({
                            "mac": src, "object": f"{otype}:{inst}",
                            "property": prop, "value": val,
                        })

    elapsed = max(float(elapsed_s or 0.1), 0.1)
    tokens = frame_counts.get("token", 0)
    valid_frames = len(frames)
    crc_errors = header_crc_errors + data_crc_errors

    # Produce clear field-technician verdict
    verdicts = []
    health = "healthy"
    if total_bytes == 0:
        health = "silent"
        verdicts.append("No bytes received — check RS-485 wiring (A/B/GND), device power, or port selection.")
    elif valid_frames == 0:
        health = "error"
        if preambles > 0 and crc_errors > 0:
            verdicts.append(
                f"Saw {total_bytes} bytes with {preambles} MS/TP preambles but {crc_errors} CRC errors — "
                "check termination resistor (120 ohm), ground reference, or baud mismatch."
            )
        else:
            verdicts.append(
                f"Received {total_bytes} bytes with 0 valid MS/TP frames at {baud or '?'} baud — "
                "likely wrong baud rate, swapped A/B polarity, or non-MS/TP protocol (Modbus RTU / FLN)."
            )
    else:
        err_ratio = crc_errors / max(valid_frames + crc_errors, 1)
        if err_ratio > 0.10:
            health = "warning"
            verdicts.append(
                f"High CRC error rate ({crc_errors} errors / {valid_frames} valid frames) — "
                "check cable shield/ground, EOL termination, or bus stubs."
            )
        if len(masters) == 1 and self_tokens:
            verdicts.append(
                f"Only 1 master on trunk (MAC {sorted(masters)[0]}) passing token to itself — "
                "normal for an isolated single device, or another controller isn't replying."
            )
        if not verdicts:
            verdicts.append(
                f"Healthy MS/TP trunk at {baud or '?'} baud: {len(masters)} active master(s), "
                f"{tokens / elapsed:.1f} tokens/sec, {crc_errors} CRC error(s)."
            )

    return {
        "baud": baud,
        "duration_s": round(elapsed, 2),
        "total_bytes": total_bytes,
        "valid_frames": valid_frames,
        "preambles": preambles,
        "header_crc_errors": header_crc_errors,
        "data_crc_errors": data_crc_errors,
        "tokens_per_sec": round(tokens / elapsed, 2),
        "active_masters": [
            {"mac": mac, "hex": f"0x{mac:02X}", "frames": cnt}
            for mac, cnt in sorted(masters.items())
        ],
        "frame_counts": dict(frame_counts),
        "token_ring": dict(token_edges.most_common(12)),
        "iam_devices": devices,
        "passive_points": passive_points[:100],
        "health": health,
        "verdicts": verdicts,
    }


def diagnose_port(port, baud=None, duration_s=5.0, sweep_bauds=None):
    """Passively sniff `port` for `duration_s` seconds and return a Bus Doctor report.

    Claims the port in runtime_state so discovery.py does not contend on it.
    If `baud` is None/0, sweeps candidate bauds briefly until valid MS/TP frames appear.
    """
    try:
        import serial
    except ImportError as e:
        raise RuntimeError("pyserial is not installed on this host") from e

    duration_s = max(1.0, min(float(duration_s or 5.0), 30.0))
    if not STATE.claim_port(port):
        raise RuntimeError(f"{port} is currently busy with another diagnostic")

    try:
        candidates = [int(baud)] if baud else list(sweep_bauds or [38400, 19200, 9600, 76800, 57600, 115200])
        best_report = None
        per_baud = duration_s if len(candidates) == 1 else max(1.5, duration_s / len(candidates))
        for b in candidates:
            raw = bytearray()
            t0 = time.monotonic()
            ser = serial.Serial(port=port, baudrate=b, parity="N", stopbits=1, bytesize=8, timeout=0.1)
            try:
                deadline = t0 + per_baud
                while time.monotonic() < deadline:
                    chunk = ser.read(512)
                    if chunk:
                        raw.extend(chunk)
            finally:
                ser.close()
            elapsed = time.monotonic() - t0
            report = analyze_mstp_bytes(raw, elapsed_s=elapsed, baud=b)
            report["port"] = port
            if report["valid_frames"] > 0:
                return report
            if best_report is None or report["total_bytes"] > best_report["total_bytes"]:
                best_report = report
        return best_report or {"port": port, "health": "silent", "verdicts": ["No data captured."]}
    finally:
        STATE.release_port(port)
