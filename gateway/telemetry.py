"""Live Pi & gateway hardware/system telemetry (stdlib only).

Works on Raspberry Pi 5 (reads thermal zone, vcgencmd throttled state,
/sys/class/rtc/rtc0 battery & charging voltage, ethtool/sysfs eth0 link state,
systemd service status, memory/load/disk) and degrades gracefully on non-Pi
or Windows development machines.
"""

import os
import platform
import shutil
import subprocess
import time


def _read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return None


def _run_short(cmd, timeout=2.0):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "").strip()
    except Exception:  # noqa: BLE001
        return -1, ""


def _cpu_temp_c():
    raw = _read_text("/sys/class/thermal/thermal_zone0/temp")
    if raw is not None:
        try:
            return round(int(raw) / 1000.0, 1)
        except ValueError:
            pass
    return None


def _throttled():
    rc, out = _run_short(["vcgencmd", "get_throttled"])
    if rc == 0 and "=" in out:
        hex_val = out.split("=", 1)[1].strip()
        try:
            bits = int(hex_val, 16)
        except ValueError:
            bits = 0
        flags = []
        if bits & 0x1:
            flags.append("under-voltage")
        if bits & 0x2:
            flags.append("freq-capped")
        if bits & 0x4:
            flags.append("throttled")
        if bits & 0x10000:
            flags.append("past-under-voltage")
        if bits & 0x40000:
            flags.append("past-throttled")
        return {"raw": hex_val, "ok": bits == 0, "flags": flags}
    return {"raw": None, "ok": True, "flags": []}


def _rtc_info():
    batt_uv = _read_text("/sys/class/rtc/rtc0/battery_voltage")
    chg_uv = _read_text("/sys/class/rtc/rtc0/charging_voltage")
    rtc_date = _read_text("/sys/class/rtc/rtc0/date")
    rtc_time = _read_text("/sys/class/rtc/rtc0/time")
    return {
        "battery_v": round(int(batt_uv) / 1_000_000.0, 2) if batt_uv and batt_uv.isdigit() else None,
        "charging_v": round(int(chg_uv) / 1_000_000.0, 2) if chg_uv and chg_uv.isdigit() else None,
        "rtc_utc": f"{rtc_date} {rtc_time}" if (rtc_date and rtc_time) else None,
    }


def _eth0_info():
    speed = _read_text("/sys/class/net/eth0/speed")
    duplex = _read_text("/sys/class/net/eth0/duplex")
    carrier = _read_text("/sys/class/net/eth0/carrier")
    oper = _read_text("/sys/class/net/eth0/operstate")
    eee = None
    rc, out = _run_short(["/usr/sbin/ethtool", "--show-eee", "eth0"])
    if rc == 0:
        for line in out.splitlines():
            if "eee status" in line.lower():
                eee = line.split(":", 1)[1].strip()
                break
    return {
        "operstate": oper,
        "carrier": carrier == "1" if carrier is not None else None,
        "speed_mbps": int(speed) if speed and speed.isdigit() else None,
        "duplex": duplex,
        "eee": eee,
    }


def _memory_info():
    raw = _read_text("/proc/meminfo")
    if not raw:
        return {"total_mb": None, "available_mb": None, "used_pct": None}
    vals = {}
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].isdigit():
            vals[parts[0].rstrip(":")] = int(parts[1])
    total = vals.get("MemTotal")
    avail = vals.get("MemAvailable")
    if not total:
        return {"total_mb": None, "available_mb": None, "used_pct": None}
    used_pct = round(((total - (avail or 0)) / total) * 100.0, 1)
    return {
        "total_mb": round(total / 1024.0, 0),
        "available_mb": round((avail or 0) / 1024.0, 0),
        "used_pct": used_pct,
    }


def _uptime_seconds():
    raw = _read_text("/proc/uptime")
    if raw:
        try:
            return int(float(raw.split()[0]))
        except ValueError:
            pass
    return None


def _services_info():
    out = {}
    if platform.system() != "Linux":
        return out
    for svc in ("predator-gateway", "wayvnc", "eth0-stable-watch"):
        rc, status = _run_short(["systemctl", "is-active", svc])
        out[svc] = status if status else ("active" if rc == 0 else "unknown")
    return out


def _disk_info():
    try:
        usage = shutil.disk_usage(os.path.expanduser("~"))
        return {
            "total_gb": round(usage.total / (1024 ** 3), 1),
            "free_gb": round(usage.free / (1024 ** 3), 1),
            "used_pct": round((usage.used / usage.total) * 100.0, 1) if usage.total else 0.0,
        }
    except OSError:
        return {"total_gb": None, "free_gb": None, "used_pct": None}


def collect():
    """Return a JSON-serializable telemetry dict for the Web UI and Explorer GUI."""
    load1 = None
    try:
        load1 = round(os.getloadavg()[0], 2)
    except (OSError, AttributeError):
        pass
    temp_c = _cpu_temp_c()
    temp_f = round((temp_c * 9.0 / 5.0) + 32.0, 1) if temp_c is not None else None
    return {
        "hostname": platform.node(),
        "platform": platform.platform(),
        "local_time": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "uptime_s": _uptime_seconds(),
        "cpu_temp_f": temp_f,
        "cpu_temp_c": temp_c,
        "load_1m": load1,
        "throttled": _throttled(),
        "memory": _memory_info(),
        "disk": _disk_info(),
        "rtc": _rtc_info(),
        "eth0": _eth0_info(),
        "services": _services_info(),
    }
