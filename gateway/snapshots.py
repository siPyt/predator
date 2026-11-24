"""Named site/equipment snapshots and before/after point diffing.

Saves discovered rows + runtime state as JSON files in [snapshots] dir
(default ~/predator_snapshots) so field engineers can capture named scans
like 'Chiller_1' or 'AHU_4_Before' and diff any two snapshots (or diff a
saved snapshot against the live scan).
"""

import json
import os
import re
import time


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def snapshots_dir(cfg):
    if cfg.has_section("snapshots") and cfg.has_option("snapshots", "dir"):
        return os.path.expanduser(cfg.get("snapshots", "dir"))
    return os.path.expanduser("~/predator_snapshots")


def _safe_id(name):
    cleaned = _SAFE_NAME_RE.sub("_", str(name or "").strip()).strip("._-")
    return cleaned or time.strftime("scan_%Y%m%d_%H%M%S")


def _row_key(row):
    return (
        str(row.get("source", "")),
        str(row.get("address", "")),
        str(row.get("object_or_register", "")),
    )


def save_snapshot(cfg, name, rows, meta=None):
    base = snapshots_dir(cfg)
    os.makedirs(base, exist_ok=True)
    snap_id = _safe_id(name)
    path = os.path.join(base, f"{snap_id}.json")
    payload = {
        "id": snap_id,
        "name": str(name or snap_id).strip(),
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ts": time.time(),
        "row_count": len(rows),
        "meta": meta or {},
        "rows": list(rows),
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    os.replace(tmp, path)
    return payload


def list_snapshots(cfg):
    base = snapshots_dir(cfg)
    if not os.path.isdir(base):
        return []
    out = []
    for fname in sorted(os.listdir(base)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(base, fname)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            out.append({
                "id": data.get("id") or fname[:-5],
                "name": data.get("name") or fname[:-5],
                "saved_at": data.get("saved_at", ""),
                "ts": data.get("ts", 0),
                "row_count": data.get("row_count", len(data.get("rows") or [])),
            })
        except Exception:  # noqa: BLE001
            continue
    out.sort(key=lambda s: s.get("ts", 0), reverse=True)
    return out


def load_snapshot(cfg, snap_id):
    base = snapshots_dir(cfg)
    safe = _safe_id(snap_id)
    path = os.path.join(base, f"{safe}.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"snapshot {snap_id!r} not found")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def delete_snapshot(cfg, snap_id):
    base = snapshots_dir(cfg)
    safe = _safe_id(snap_id)
    path = os.path.join(base, f"{safe}.json")
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def diff_rows(before_rows, after_rows):
    """Compare two lists of discovered CSV-style rows.

    Returns {"added": [...], "removed": [...], "changed": [...], "unchanged_count": int}.
    """
    before_map = {_row_key(r): r for r in before_rows}
    after_map = {_row_key(r): r for r in after_rows}

    added = []
    removed = []
    changed = []
    unchanged = 0

    for k, after in after_map.items():
        if k not in before_map:
            added.append(after)
        else:
            before = before_map[k]
            diffs = {}
            for field in ("description", "value", "units", "writable", "bus_or_network"):
                bval = str(before.get(field, ""))
                aval = str(after.get(field, ""))
                if bval != aval:
                    diffs[field] = {"before": bval, "after": aval}
            if diffs:
                changed.append({
                    "source": after.get("source", ""),
                    "address": after.get("address", ""),
                    "object_or_register": after.get("object_or_register", ""),
                    "description": after.get("description", ""),
                    "changes": diffs,
                })
            else:
                unchanged += 1

    for k, before in before_map.items():
        if k not in after_map:
            removed.append(before)

    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "unchanged_count": unchanged,
    }
