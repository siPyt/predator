"""Predator's on-Pi Explorer GUI — clean, minimal dark theme matching the
Kali dragon wallpaper & predator-icon.png logo (stdlib Tkinter only).

Three focused tabs (zero clutter):
  - Points: clean 6-column live table with search/filter, column sort,
    scroll/selection preservation, and double-click live Read/Write dialog
  - Mirius / ICC: Baud/MAC/Port B FLN controls, point toggle list, Rebuild,
    and one-click Open in ICC Configuration Studio
  - Tools: dropdown switcher showing one tool at a time (Serial Ports,
    Bus Doctor, Multi-Protocol Recon, Snapshots & Diff)
"""

import csv
import json
import os
import subprocess
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
from tkinter import messagebox, ttk

from . import config as cfgmod

REFRESH_MS = 3500
API_BASE = "http://localhost:8099"
ICON_PATH = os.path.join(os.path.dirname(__file__), "assets", "predator-icon.png")

_BG = "#0a0b0e"
_PANEL = "#12141a"
_PANEL2 = "#181b23"
_FG = "#d8dce3"
_BONE = "#eceae2"
_MUTED = "#7f8796"
_ACCENT = "#39d353"


def _api_get(path, timeout=5):
    with urllib.request.urlopen(f"{API_BASE}{path}", timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _api_post(path, payload=None, timeout=15):
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}{path}", data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _set_window_icon(root):
    try:
        icon = tk.PhotoImage(file=ICON_PATH)
        root.iconphoto(True, icon)
        root._predator_icon_ref = icon
    except Exception:  # noqa: BLE001
        pass


def _apply_dark_theme(root):
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    root.configure(bg=_BG)
    style.configure(".", background=_BG, foreground=_FG, fieldbackground=_PANEL)
    style.configure("TFrame", background=_BG)
    style.configure("Panel.TFrame", background=_PANEL)
    style.configure("TLabel", background=_BG, foreground=_FG)
    style.configure("Panel.TLabel", background=_PANEL, foreground=_MUTED)
    style.configure("Muted.TLabel", background=_BG, foreground=_MUTED)
    style.configure("TButton", background=_PANEL2, foreground=_BONE, padding=(10, 4))
    style.map("TButton", background=[("active", "#242834")], foreground=[("active", "#ffffff")])
    style.configure("TCheckbutton", background=_BG, foreground=_FG)
    style.configure("TEntry", fieldbackground=_PANEL, foreground=_BONE, insertcolor=_BONE)
    style.configure("TCombobox", fieldbackground="#ffffff", background=_PANEL2, foreground="#000000", arrowcolor=_BONE)
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", "#ffffff"), ("!disabled", "#ffffff")],
        foreground=[("readonly", "#000000"), ("!disabled", "#000000")],
        selectbackground=[("readonly", "#ffffff")],
        selectforeground=[("readonly", "#000000")],
    )
    root.option_add("*TCombobox*Listbox.background", "#ffffff")
    root.option_add("*TCombobox*Listbox.foreground", "#000000")
    root.option_add("*TCombobox*Listbox.selectBackground", "#232938")
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    style.configure("TNotebook", background=_BG, borderwidth=0)
    style.configure("TNotebook.Tab", background=_PANEL, foreground=_MUTED, padding=(14, 6))
    style.map("TNotebook.Tab", background=[("selected", _BG)], foreground=[("selected", _BONE)])
    style.configure("Treeview", background=_PANEL, fieldbackground=_PANEL, foreground=_FG, rowheight=24)
    style.map("Treeview", background=[("selected", "#232938")], foreground=[("selected", "#ffffff")])
    style.configure("Treeview.Heading", background=_PANEL2, foreground=_MUTED)
    style.configure("Header.TLabel", background=_BG, foreground=_BONE, font=("TkDefaultFont", 10, "bold"))
    style.configure("Brand.TLabel", background=_PANEL, foreground=_BONE, font=("TkDefaultFont", 12, "bold"))


class PointRwDialog(tk.Toplevel):
    """Modal dialog for live BACnet ReadProperty / WriteProperty / Relinquish."""

    def __init__(self, parent, row):
        super().__init__(parent)
        self.row = row
        self.title(row.get("description") or row.get("object_or_register", "Point"))
        self.configure(bg=_BG)
        self.geometry("420x250")
        self.transient(parent)

        frm = ttk.Frame(self, padding=14)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text=row.get("description") or row.get("object_or_register"), style="Header.TLabel").pack(anchor="w")
        ttk.Label(frm, text=f"{row.get('source')} · Device {row.get('address')} · {row.get('object_or_register')}",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 10))

        rf = ttk.Frame(frm)
        rf.pack(fill="x", pady=(0, 12))
        self.val_out = tk.StringVar(value=f"{row.get('value', '')} {row.get('units', '')}".strip() or "—")
        ttk.Label(rf, textvariable=self.val_out, style="Header.TLabel").pack(side="left")
        ttk.Button(rf, text="Read Live", command=self._do_read).pack(side="right")

        wf = ttk.Frame(frm)
        wf.pack(fill="x", pady=4)
        ttk.Label(wf, text="Value:").pack(side="left")
        self.write_var = tk.StringVar()
        ttk.Entry(wf, textvariable=self.write_var, width=18).pack(side="left", padx=6)
        ttk.Label(wf, text="Pri:").pack(side="left")
        self.pri_var = tk.StringVar(value="8")
        ttk.Spinbox(wf, from_=1, to=16, textvariable=self.pri_var, width=4).pack(side="left", padx=4)

        bf = ttk.Frame(frm)
        bf.pack(fill="x", pady=(18, 0))
        ttk.Button(bf, text="Write", command=lambda: self._do_write(False)).pack(side="left", padx=(0, 6))
        ttk.Button(bf, text="Relinquish", command=lambda: self._do_write(True)).pack(side="left", padx=(0, 6))
        ttk.Button(bf, text="?", width=3, command=self._show_help).pack(side="left")
        ttk.Button(bf, text="Close", command=self.destroy).pack(side="right")

    def _show_help(self):
        messagebox.showinfo(
            "Point Read / Write Help",
            "Read Live: reads the current value from the device.\n\n"
            "Value: enter a number for analog (e.g. 44.0), active/inactive for binary, or an integer (e.g. 2) for multi-state.\n\n"
            "Pri (1-16): BACnet command priority (8 = operator override, 16 = lowest).\n\n"
            "Relinquish: sends NULL to release your test override at that priority so normal control takes back over.",
            parent=self,
        )

    def _do_read(self):
        self.val_out.set("reading...")
        self.update_idletasks()
        try:
            res = _api_post("/point/read", {
                "source": self.row.get("source"), "address": self.row.get("address"),
                "object": self.row.get("object_or_register"), "bus": self.row.get("bus_or_network"),
            })
            self.val_out.set(f"{res.get('value')} {res.get('units', '')}".strip())
        except Exception as e:  # noqa: BLE001
            self.val_out.set(f"Error: {e}")

    def _do_write(self, relinquish):
        try:
            val = None if relinquish else self.write_var.get()
            _api_post("/point/write", {
                "source": self.row.get("source"), "address": self.row.get("address"),
                "object": self.row.get("object_or_register"), "bus": self.row.get("bus_or_network"),
                "value": val, "priority": int(self.pri_var.get() or "8"),
            })
            self._do_read()
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Predator", f"Write failed:\n{e}", parent=self)


class PointsTab:
    """Clean 6-column live points view with search, sort, and double-click R/W."""

    COLS = ("address", "object_or_register", "description", "value", "writable", "source")
    LABELS = {"address": "Device", "object_or_register": "Object", "description": "Name",
              "value": "Value", "writable": "R/W", "source": "Protocol"}

    def __init__(self, parent, csv_path):
        self.csv_path = csv_path
        self.frame = ttk.Frame(parent, padding=10)
        self._all_rows = []
        self._row_by_iid = {}
        self._sort_col = "address"
        self._sort_asc = True

        top = ttk.Frame(self.frame)
        top.pack(fill="x", pady=(0, 8))
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self._render())
        ttk.Entry(top, textvariable=self.filter_var, width=30).pack(side="left")

        self.source_var = tk.StringVar(value="all")
        cb = ttk.Combobox(top, textvariable=self.source_var, width=13, state="readonly",
                          values=("all", "mstp", "bacnet-ip", "modbus-rtu", "points-map"))
        cb.pack(side="left", padx=8)
        cb.bind("<<ComboboxSelected>>", lambda *_: self._render())

        self.status_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.status_var, style="Muted.TLabel").pack(side="right")

        wrap = ttk.Frame(self.frame)
        wrap.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(wrap, columns=self.COLS, show="headings")
        widths = {"address": 85, "object_or_register": 155, "description": 300, "value": 130, "writable": 65, "source": 95}
        for col in self.COLS:
            self.tree.heading(col, text=self.LABELS[col], command=lambda c=col: self._toggle_sort(c))
            self.tree.column(col, width=widths[col], anchor="w")
        yscroll = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        yscroll.pack(side="right", fill="y")

        ttk.Label(self.frame, text="Double-click any BACnet point to read or test-write live.",
                  style="Muted.TLabel").pack(anchor="w", pady=(6, 0))
        self.tree.bind("<Double-1>", lambda _e: self._open_selected())
        self.refresh()

    def _toggle_sort(self, col):
        self._sort_asc = (not self._sort_asc) if self._sort_col == col else True
        self._sort_col = col
        self._render()

    def _open_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        row = self._row_by_iid.get(sel[0])
        if row and row.get("source") in ("mstp", "bacnet-ip") and ":" in str(row.get("object_or_register", "")):
            PointRwDialog(self.frame, row)

    def _render(self):
        q = self.filter_var.get().strip().lower()
        src = self.source_var.get()
        rows = [
            r for r in self._all_rows
            if (src == "all" or str(r.get("source", "")).startswith(src))
            and (not q or q in " ".join(str(r.get(c, "")) for c in self.COLS).lower())
        ]

        def _skey(r):
            v = str(r.get(self._sort_col, ""))
            try:
                return (0, float(v), v)
            except ValueError:
                return (1, 0.0, v.lower())

        rows.sort(key=_skey, reverse=not self._sort_asc)
        yview = self.tree.yview()
        sel_keys = {
            (self._row_by_iid[i].get("source"), self._row_by_iid[i].get("address"), self._row_by_iid[i].get("object_or_register"))
            for i in self.tree.selection() if i in self._row_by_iid
        }

        self.tree.delete(*self.tree.get_children())
        self._row_by_iid.clear()
        reselect = []
        for r in rows:
            val_s = f"{r.get('value', '')} {r.get('units', '')}".strip() if r.get("value", "") != "" else ""
            rw_s = "" if r.get("writable", "") == "" else ("R/W" if str(r.get("writable")).lower() == "true" else "R")
            iid = self.tree.insert("", "end", values=(
                r.get("address", ""), r.get("object_or_register", ""),
                r.get("description", ""), val_s, rw_s, r.get("source", ""),
            ))
            self._row_by_iid[iid] = r
            if (r.get("source"), r.get("address"), r.get("object_or_register")) in sel_keys:
                reselect.append(iid)
        if reselect:
            self.tree.selection_set(reselect)
        if yview:
            self.tree.yview_moveto(yview[0])
        self.status_var.set(f"{len(rows)} point(s)")

    def refresh(self):
        try:
            try:
                self._all_rows = _api_get("/rows").get("rows", [])
            except Exception:  # noqa: BLE001
                self._all_rows = []
                if os.path.exists(self.csv_path):
                    with open(self.csv_path, newline="", encoding="utf-8") as f:
                        self._all_rows = list(csv.DictReader(f))
            self._render()
        except Exception as e:  # noqa: BLE001
            self.status_var.set(f"error: {e}")
        self.frame.after(REFRESH_MS, self.refresh)


class MiriusTab:
    """Clean Mirius / ICC configurator + Open in ICC Studio."""

    COLS = ("use", "device", "mac", "obj", "name", "db", "flt", "fln_pt", "fln_desc")

    def __init__(self, parent):
        self.frame = ttk.Frame(parent, padding=10)
        self._points = []
        self._excluded = set()
        self._floats = set()
        self._desktop_icsproj = os.path.expanduser("~/Desktop/Predator_Mirius_BACnet.icsproj")

        top = ttk.Frame(self.frame)
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text="Baud:").pack(side="left")
        self.baud_var = tk.StringVar(value="19200")
        ttk.Entry(top, textvariable=self.baud_var, width=8).pack(side="left", padx=(4, 10))

        ttk.Label(top, text="MAC:").pack(side="left")
        self.mac_var = tk.StringVar(value="1")
        ttk.Entry(top, textvariable=self.mac_var, width=5).pack(side="left", padx=(4, 10))

        ttk.Label(top, text="Port B:").pack(side="left")
        self.port_b_var = tk.StringVar(value="none")
        ttk.Combobox(top, textvariable=self.port_b_var, width=14, state="readonly",
                     values=("none", "bacnet-mstp", "fln", "modbus-rtu", "metasys-n2")).pack(side="left", padx=(4, 8))

        ttk.Button(top, text="Open in ICC Studio", command=self._open_studio).pack(side="right", padx=(6, 0))
        ttk.Button(top, text="Save & Rebuild", command=self._save).pack(side="right")
        ttk.Button(top, text="Fill Demo", command=self._fill_demo).pack(side="right", padx=(0, 6))

        bar2 = ttk.Frame(self.frame)
        bar2.pack(fill="x", pady=(0, 8))
        self.summary_var = tk.StringVar(value="")
        ttk.Label(bar2, textvariable=self.summary_var, style="Muted.TLabel").pack(side="left")
        ttk.Button(bar2, text="Toggle Float32", command=self._toggle_float).pack(side="right", padx=(6, 0))
        ttk.Button(bar2, text="Toggle Use", command=self._toggle_use).pack(side="right")

        self.tree = ttk.Treeview(self.frame, columns=self.COLS, show="headings")
        widths = {"use": 42, "device": 65, "mac": 45, "obj": 70, "name": 260, "db": 55, "flt": 55, "fln_pt": 55, "fln_desc": 120}
        for c in self.COLS:
            self.tree.heading(c, text=c.upper())
            self.tree.column(c, width=widths[c], anchor="w")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda _e: self._toggle_use())

    def load(self):
        try:
            p = _api_get("/icc/preview")
            self._points = p.get("points") or []
            self._excluded = {pt["key"] for pt in self._points if not pt.get("included", True)}
            self._floats = {pt["key"] for pt in self._points if pt.get("use_float")}
            self._desktop_icsproj = p.get("desktop_icsproj") or self._desktop_icsproj
            self.baud_var.set(str(p.get("baud") or 19200))
            self.mac_var.set(str(p.get("default_mac") or 1))
            self.port_b_var.set(p.get("port_b_protocol") or ("fln" if p.get("include_port_b") else "none"))
            self.summary_var.set(f"{p.get('included_count', 0)}/{p.get('total_count', 0)} points · Double-click row to toggle")
            self._render()
        except Exception as e:  # noqa: BLE001
            self.summary_var.set(f"Error: {e}")

    def _render(self):
        self.tree.delete(*self.tree.get_children())
        for pt in self._points:
            k = pt["key"]
            self.tree.insert("", "end", iid=k, values=(
                "[x]" if k not in self._excluded else "[ ]",
                pt["device_instance"], pt["mac"], f"{pt['obj_type']}:{pt['instance']}",
                pt["name"], pt["db_addr"],
                "32f" if k in self._floats else ("16u" if pt["kind"] == "analog" else "-"),
                pt.get("fln_point") or "-", pt["fln_desc"],
            ))

    def _toggle_use(self):
        for k in self.tree.selection():
            self._excluded.remove(k) if k in self._excluded else self._excluded.add(k)
        self._render()

    def _toggle_float(self):
        by_k = {pt["key"]: pt for pt in self._points}
        for k in self.tree.selection():
            if by_k.get(k, {}).get("kind") == "analog":
                self._floats.remove(k) if k in self._floats else self._floats.add(k)
        self._render()

    def _save(self):
        try:
            _api_post("/icc/config", {
                "baud_override": int(self.baud_var.get() or "19200"),
                "default_mac": int(self.mac_var.get() or "1"),
                "port_b_protocol": self.port_b_var.get() or "none",
                "excluded_keys": sorted(self._excluded),
                "float_keys": sorted(self._floats),
            })
            messagebox.showinfo("Predator", "Mirius project & Excel point map rebuilt.")
            self.load()
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Predator", f"Save failed:\n{e}")

    def _fill_demo(self):
        try:
            _api_post("/demo/load", {})
            self.load()
            self.baud_var.set("19200")
            self.mac_var.set("1")
            self.port_b_var.set("bacnet-mstp")
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Predator", str(e))

    def _open_studio(self):
        launcher = os.path.expanduser("~/.local/bin/icc-config-studio")
        if not os.path.exists(launcher):
            messagebox.showinfo("Predator", f"ICC Studio launcher not found at {launcher}")
            return
        cmd = [launcher, self._desktop_icsproj] if os.path.exists(self._desktop_icsproj) else [launcher]
        subprocess.Popen(cmd, start_new_session=True)


class ToolsTab:
    """One focused tool at a time: Serial Ports, Bus Doctor, Recon, or Snapshots."""

    def __init__(self, parent):
        self.frame = ttk.Frame(parent, padding=10)
        bar = ttk.Frame(self.frame)
        bar.pack(fill="x", pady=(0, 10))
        ttk.Label(bar, text="Tool:").pack(side="left")
        self.tool_var = tk.StringVar(value="Serial Ports")
        cb = ttk.Combobox(bar, textvariable=self.tool_var, width=20, state="readonly",
                          values=("Serial Ports", "Bus Doctor", "Network Recon", "Snapshots & Diff"))
        cb.pack(side="left", padx=8)
        cb.bind("<<ComboboxSelected>>", lambda *_: self._switch())

        self.container = ttk.Frame(self.frame)
        self.container.pack(fill="both", expand=True)

        self.panes = {
            "Serial Ports": self._build_ports(),
            "Bus Doctor": self._build_doctor(),
            "Network Recon": self._build_recon(),
            "Snapshots & Diff": self._build_snapshots(),
        }
        self._switch()

    def _switch(self):
        for p in self.panes.values():
            p.pack_forget()
        name = self.tool_var.get()
        self.panes[name].pack(fill="both", expand=True)
        if name == "Serial Ports":
            self.load_ports()
        elif name == "Snapshots & Diff":
            self.load_snapshots()

    def _build_ports(self):
        f = ttk.Frame(self.container)
        self._ports_rows = ttk.Frame(f)
        self._ports_rows.pack(fill="x")
        self._port_vars = []
        return f

    def load_ports(self):
        for w in self._ports_rows.winfo_children():
            w.destroy()
        self._port_vars.clear()
        try:
            data = _api_get("/ports")
        except Exception as e:  # noqa: BLE001
            ttk.Label(self._ports_rows, text=f"API error: {e}", style="Muted.TLabel").pack(anchor="w")
            return
        if not data.get("ports"):
            ttk.Label(self._ports_rows, text="No USB-RS485 adapters detected.", style="Muted.TLabel").pack(anchor="w")
            return
        pC = ["auto"] + data["protocol_choices"]
        bC = [str(b) for b in data["baud_choices"]]
        parC = data["parity_choices"]
        for i, p in enumerate(data["ports"]):
            port, st, ov = p["port"], p["state"] or {}, p["override"]
            ttk.Label(self._ports_rows, text=port).grid(row=i, column=0, padx=4, pady=4, sticky="w")
            ttk.Label(self._ports_rows, text=st.get("note") or "-", style="Muted.TLabel").grid(row=i, column=1, padx=6, sticky="w")
            pv = tk.StringVar(value=ov["protocol"] if ov else "auto")
            bv = tk.StringVar(value=str(ov["baud"] if ov else (st.get("baud") or bC[0])))
            prv = tk.StringVar(value=ov["parity"] if ov else "N")
            ttk.Combobox(self._ports_rows, textvariable=pv, values=pC, width=12, state="readonly").grid(row=i, column=2, padx=3)
            ttk.Combobox(self._ports_rows, textvariable=bv, values=bC, width=8, state="readonly").grid(row=i, column=3, padx=3)
            ttk.Combobox(self._ports_rows, textvariable=prv, values=parC, width=4, state="readonly").grid(row=i, column=4, padx=3)
            ttk.Button(self._ports_rows, text="Apply", command=lambda pt=port, a=pv, b=bv, c=prv: self._apply_port(pt, a, b, c)).grid(row=i, column=5, padx=4)
            self._port_vars.append((pv, bv, prv))

    def _apply_port(self, port, pv, bv, prv):
        try:
            if pv.get() == "auto":
                _api_post("/ports/auto", {"port": port})
            else:
                _api_post("/ports/override", {"port": port, "protocol": pv.get(), "baud": int(bv.get()), "parity": prv.get()})
            self.load_ports()
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Predator", str(e))

    def _build_doctor(self):
        f = ttk.Frame(self.container)
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(0, 8))
        self.bd_port = tk.StringVar(value="/dev/ttyUSB0")
        ttk.Entry(bar, textvariable=self.bd_port, width=16).pack(side="left")
        self.bd_baud = tk.StringVar(value="38400")
        ttk.Combobox(bar, textvariable=self.bd_baud, width=9, state="readonly",
                     values=("0", "9600", "19200", "38400", "76800", "115200")).pack(side="left", padx=6)
        ttk.Button(bar, text="Sniff 5s (Passive)", command=self._run_doctor).pack(side="left")
        ttk.Button(bar, text="Demo", command=self._demo_doctor).pack(side="left", padx=6)
        self.bd_out = tk.Text(f, bg="#07080a", fg="#b9c2d0", relief="flat", font=("TkFixedFont", 9))
        self.bd_out.pack(fill="both", expand=True)
        self.bd_out.insert("1.0", "Listen-only MS/TP diagnostic. Never transmits.")
        return f

    def _demo_doctor(self):
        try:
            _api_post("/demo/load", {})
            self.bd_port.set("/dev/ttyUSB0")
            self.bd_baud.set("19200")
            self._poll_doctor()
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Predator", str(e))

    def _run_doctor(self):
        try:
            _api_post("/bus-doctor", {"port": self.bd_port.get().strip(), "baud": int(self.bd_baud.get() or "0"), "duration_s": 5})
            self.bd_out.delete("1.0", "end")
            self.bd_out.insert("1.0", "Listening...")
            self.frame.after(1000, self._poll_doctor)
        except Exception as e:  # noqa: BLE001
            self.bd_out.delete("1.0", "end")
            self.bd_out.insert("1.0", f"Error: {e}")

    def _poll_doctor(self):
        try:
            r = _api_get("/bus-doctor")
            if r.get("running"):
                self.frame.after(1000, self._poll_doctor)
                return
            self.bd_out.delete("1.0", "end")
            if r.get("error"):
                self.bd_out.insert("1.0", f"Error: {r['error']}")
                return
            rep = r.get("report") or {}
            self.bd_out.insert("1.0", "\n".join([
                f"{rep.get('port')} @ {rep.get('baud')} — {str(rep.get('health', '')).upper()}",
                f"Bytes: {rep.get('total_bytes')} | Frames: {rep.get('valid_frames')} | Tokens/s: {rep.get('tokens_per_sec')}",
                "", *(rep.get("verdicts") or []),
            ]))
        except Exception:  # noqa: BLE001
            pass

    def _build_recon(self):
        f = ttk.Frame(self.container)
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(0, 8))
        self.recon_protos = tk.StringVar(value="all")
        ttk.Entry(bar, textvariable=self.recon_protos, width=26).pack(side="left")
        ttk.Button(bar, text="Run Network Recon", command=self._start_recon).pack(side="left", padx=6)
        ttk.Button(bar, text="Demo", command=self._demo_recon).pack(side="left")
        self.recon_out = tk.Text(f, bg="#07080a", fg="#b9c2d0", relief="flat", font=("TkFixedFont", 9))
        self.recon_out.pack(fill="both", expand=True)
        return f

    def _demo_recon(self):
        try:
            _api_post("/demo/load", {})
            self.recon_protos.set("bacnet,modbus,profinet,s7,mqtt")
            self._poll_recon()
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Predator", str(e))

    def _start_recon(self):
        try:
            protos = [p.strip() for p in self.recon_protos.get().split(",") if p.strip()] or ["all"]
            _api_post("/recon/start", {"protocols": protos})
            self._poll_recon()
        except Exception as e:  # noqa: BLE001
            self.recon_out.insert("end", f"\nError: {e}")

    def _poll_recon(self):
        try:
            s = _api_get("/recon/status")
            self.recon_out.delete("1.0", "end")
            self.recon_out.insert("1.0", "\n".join(s.get("logs") or ["Running..."]))
            if s.get("running"):
                self.frame.after(1500, self._poll_recon)
        except Exception:  # noqa: BLE001
            pass

    def _build_snapshots(self):
        f = ttk.Frame(self.container)
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(0, 8))
        self.snap_name = tk.StringVar()
        ttk.Entry(bar, textvariable=self.snap_name, width=18).pack(side="left")
        ttk.Button(bar, text="Save", command=self._save_snap).pack(side="left", padx=6)
        self.before_var = tk.StringVar()
        self.before_cb = ttk.Combobox(bar, textvariable=self.before_var, width=16, state="readonly")
        self.before_cb.pack(side="left", padx=(12, 4))
        self.after_var = tk.StringVar(value="live")
        self.after_cb = ttk.Combobox(bar, textvariable=self.after_var, width=16, state="readonly")
        self.after_cb.pack(side="left", padx=4)
        ttk.Button(bar, text="Diff", command=self._diff).pack(side="left", padx=6)
        ttk.Button(bar, text="Demo Diff", command=self._demo_diff).pack(side="left")
        self.diff_out = tk.Text(f, bg="#07080a", fg=_FG, relief="flat", font=("TkFixedFont", 9))
        self.diff_out.pack(fill="both", expand=True)
        return f

    def load_snapshots(self):
        try:
            ids = [s["id"] for s in (_api_get("/snapshots").get("snapshots") or [])]
            self.before_cb["values"] = ids
            self.after_cb["values"] = ["live"] + ids
            if ids and not self.before_var.get():
                self.before_var.set(ids[0])
        except Exception:  # noqa: BLE001
            pass

    def _save_snap(self):
        try:
            res = _api_post("/snapshots", {"name": self.snap_name.get().strip()})
            self.snap_name.set("")
            self.load_snapshots()
            messagebox.showinfo("Predator", f"Saved '{res.get('name')}'")
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Predator", str(e))

    def _demo_diff(self):
        try:
            _api_post("/demo/load", {})
            self.snap_name.set("Chiller_1_After")
            self.load_snapshots()
            self.before_var.set("Demo_Chiller_Before")
            self.after_var.set("live")
            self._diff()
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Predator", str(e))

    def _diff(self):
        b, a = self.before_var.get(), self.after_var.get() or "live"
        if not b:
            return
        try:
            d = _api_get("/snapshots/diff?" + urllib.parse.urlencode({"before": b, "after": a}))
            lines = [f"+{len(d['added'])} added · -{len(d['removed'])} removed · ~{len(d['changed'])} changed", ""]
            lines += [f"[+] {r.get('address')} {r.get('object_or_register')} {r.get('description')}" for r in d["added"]]
            lines += [f"[-] {r.get('address')} {r.get('object_or_register')} {r.get('description')}" for r in d["removed"]]
            lines += [f"[~] {r.get('address')} {r.get('object_or_register')} " + "; ".join(f"{k}: {v['before']}->{v['after']}" for k, v in r["changes"].items()) for r in d["changed"]]
            self.diff_out.delete("1.0", "end")
            self.diff_out.insert("1.0", "\n".join(lines))
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Predator", str(e))


def main():
    cfg = cfgmod.load_config()
    csv_path = cfgmod.csv_path(cfg)

    root = tk.Tk()
    root.title("PREDATOR")
    root.geometry("980x600")
    _apply_dark_theme(root)
    _set_window_icon(root)

    hdr = ttk.Frame(root, style="Panel.TFrame", padding=(14, 8))
    hdr.pack(fill="x")
    ttk.Label(hdr, text="P R E D A T O R", style="Brand.TLabel").pack(side="left")
    tel_var = tk.StringVar(value="")
    ttk.Label(hdr, textvariable=tel_var, style="Panel.TLabel").pack(side="left", padx=16)

    demo_on = {"v": False}
    demo_btn_var = tk.StringVar(value="Demo")

    def _toggle_demo():
        try:
            if not demo_on["v"]:
                _api_post("/demo/load", {})
                demo_on["v"] = True
                demo_btn_var.set("Clear Demo")
            else:
                _api_post("/demo/clear", {})
                demo_on["v"] = False
                demo_btn_var.set("Demo")
            pts.refresh()
            mirius.load()
            tools._switch()
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Predator", f"Demo error:\n{e}")

    def _show_context_help():
        idx = notebook.index(notebook.select())
        if idx == 0:
            title, msg = "Points Tab Help", (
                "Shows every live point discovered across BACnet MS/TP, BACnet/IP, Modbus RTU, and your points map.\n\n"
                "- Type in the filter box to search by name, device ID, object, or value.\n"
                "- Click any column header to sort.\n"
                "- Double-click any BACnet row to Read, Test-Write, or Relinquish live.\n"
                "- Click 'Demo' in the top bar to load sample chiller data and see how everything fills out."
            )
        elif idx == 1:
            title, msg = "Mirius / ICC Tab Help", (
                "Generates an ICC Configuration Studio project (.icsproj) and Excel point map (.xlsx) on the Pi Desktop.\n\n"
                "- Baud: MS/TP trunk speed (auto-filled from scan, e.g. 19200 or 38400).\n"
                "- MAC: chiller's MS/TP MAC address (auto-captured when device answers Who-Is).\n"
                "- Generate Port B (Siemens FLN): optional checkbox to also build the Siemens FLN Slave side matching Port A DB addresses.\n"
                "- Double-click any point row (or click Toggle Use / Toggle Float32), then click Save & Rebuild.\n"
                "- Click 'Fill Demo' to see an example setup, or 'Open in ICC Studio' to launch the project."
            )
        else:
            tname = tools.tool_var.get()
            guides = {
                "Serial Ports": (
                    "Leave Protocol on 'auto' for normal plug-and-play detection.\n\n"
                    "Only force Protocol / Baud / Parity and click Apply if a device doesn't answer Who-Is or uses a non-standard baud."
                ),
                "Bus Doctor": (
                    "Passive RS-485 MS/TP sniffer (listen-only — never transmits, safe on live trunks).\n\n"
                    "Enter the USB port (e.g. /dev/ttyUSB0), pick a baud or '0' to auto-sweep, and click Sniff 5s.\n"
                    "Reports active master MACs, token rate, CRC errors, and wiring verdicts (or click Demo for an example)."
                ),
                "Network Recon": (
                    "One-shot Ethernet sweep for industrial protocols (BACnet, Modbus TCP, MQTT, PROFINET, EtherNet/IP, S7, OPC UA, KNX).\n\n"
                    "Leave as 'all' or enter a comma list like 'bacnet,modbus,profinet,s7', then click Run Network Recon."
                ),
                "Snapshots & Diff": (
                    "Enter an equipment name (e.g. Chiller_1) and click Save to store a snapshot of all current points.\n\n"
                    "Pick a Before snapshot and an After snapshot (or 'live') and click Diff to see added [+], removed [-], and changed [~] points."
                ),
            }
            title, msg = f"Tools: {tname} Help", guides.get(tname, "")
        messagebox.showinfo(title, msg, parent=root)

    ttk.Button(hdr, text="?", width=3, command=_show_context_help).pack(side="right", padx=(6, 0))
    ttk.Button(hdr, text="Rescan", command=lambda: _api_post("/rescan", {})).pack(side="right")
    ttk.Button(hdr, textvariable=demo_btn_var, command=_toggle_demo).pack(side="right", padx=(0, 6))

    def _poll_tel():
        try:
            t = _api_get("/telemetry")
            parts = []
            if t.get("cpu_temp_f") is not None:
                parts.append(f"{t['cpu_temp_f']}°F")
            rtc = t.get("rtc") or {}
            if rtc.get("battery_v") is not None:
                parts.append(f"RTC {rtc['battery_v']}V")
            eth = t.get("eth0") or {}
            if eth.get("speed_mbps"):
                parts.append(f"{eth['speed_mbps']}M")
            tel_var.set(" · ".join(parts))
        except Exception:  # noqa: BLE001
            tel_var.set("Offline")
        root.after(6000, _poll_tel)

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True)

    pts = PointsTab(notebook, csv_path)
    mirius = MiriusTab(notebook)
    tools = ToolsTab(notebook)

    notebook.add(pts.frame, text="Points")
    notebook.add(mirius.frame, text="Mirius / ICC")
    notebook.add(tools.frame, text="Tools")

    def _on_tab(_evt):
        idx = notebook.index(notebook.select())
        if idx == 1:
            mirius.load()
        elif idx == 2:
            tools._switch()

    notebook.bind("<<NotebookTabChanged>>", _on_tab)
    _poll_tel()
    root.mainloop()


if __name__ == "__main__":
    main()
