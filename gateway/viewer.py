"""predator's on-Pi GUI: a small "explorer" app (YABE-style) that's visible
over VNC/monitor with no other software needed (standard Raspberry Pi OS
Desktop doesn't ship a spreadsheet app, and Tkinter already ships with
python3 - no extra dependency for either tab below).

Two tabs:
  - Discovered: the live discovered-points CSV (gateway/csv_export.py),
    read-only, same as before.
  - Serial Ports: one row per serial port with what auto-detect currently
    thinks is plugged in, plus manual override controls (protocol/baud/
    parity + Apply/Auto buttons) for troubleshooting when you need to force
    a setting instead of trusting auto-detect - calls gateway/api.py's
    /ports endpoints (same process the systemd service's API serves, always
    reachable at localhost regardless of whatever network the Pi is on).

Not part of the predator-gateway systemd service: this is a GUI program, so
it's started by the desktop session itself (see the XDG autostart entry
installed by scripts/install.sh).
"""

import csv
import json
import os
import tkinter as tk
import urllib.error
import urllib.request
from tkinter import messagebox, ttk

from . import config as cfgmod

REFRESH_MS = 3000
API_BASE = "http://localhost:8099"

# Dark, high-contrast theme - the "cool hacker-tool" look this was asked for,
# without needing any image/theme package beyond what ttk already ships with.
_BG = "#0d1117"
_PANEL = "#161b22"
_FG = "#c9d1d9"
_ACCENT = "#39d353"


def _api_get(path):
    with urllib.request.urlopen(f"{API_BASE}{path}", timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def _api_post(path, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}{path}", data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def _apply_dark_theme(root):
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    root.configure(bg=_BG)
    style.configure(".", background=_BG, foreground=_FG, fieldbackground=_PANEL)
    style.configure("TFrame", background=_BG)
    style.configure("TLabel", background=_BG, foreground=_FG)
    style.configure("TButton", background=_PANEL, foreground=_FG)
    style.configure("TNotebook", background=_BG, borderwidth=0)
    style.configure("TNotebook.Tab", background=_PANEL, foreground=_FG, padding=(12, 4))
    style.map("TNotebook.Tab", background=[("selected", _BG)], foreground=[("selected", _ACCENT)])
    style.configure("Treeview", background=_PANEL, fieldbackground=_PANEL, foreground=_FG, rowheight=22)
    style.configure("Treeview.Heading", background=_BG, foreground=_ACCENT)
    style.configure("Header.TLabel", background=_BG, foreground=_ACCENT, font=("TkDefaultFont", 10, "bold"))


class DiscoveredTab:
    """Read-only live view of the discovered-points CSV."""

    def __init__(self, parent, csv_path):
        self.csv_path = csv_path
        self.frame = ttk.Frame(parent)

        top = ttk.Frame(self.frame)
        top.pack(fill="x", padx=6, pady=4)
        self.status_var = tk.StringVar(value="starting...")
        ttk.Label(top, textvariable=self.status_var).pack(side="left")

        columns = ("source", "bus_or_network", "address", "object_or_register",
                   "data_type", "description", "value", "units", "writable", "last_seen")
        self.tree = ttk.Treeview(self.frame, columns=columns, show="headings")
        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=100, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        scroll = ttk.Scrollbar(self.frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.place(relx=1.0, rely=0.0, relheight=1.0, anchor="ne")

        self.refresh()

    def refresh(self):
        try:
            rows = []
            if os.path.exists(self.csv_path):
                with open(self.csv_path, newline="", encoding="utf-8") as f:
                    rows = list(csv.DictReader(f))
            self.tree.delete(*self.tree.get_children())
            for row in rows:
                self.tree.insert("", "end", values=[row.get(c, "") for c in self.tree["columns"]])
            self.status_var.set(f"{len(rows)} rows - {self.csv_path}")
        except Exception as e:  # noqa: BLE001
            self.status_var.set(f"error reading {self.csv_path}: {e}")
        self.frame.after(REFRESH_MS, self.refresh)


class SerialPortsTab:
    """Per-port auto-detect status + manual override controls, talking to
    gateway/api.py's /ports endpoints (the running predator-gateway service,
    always on localhost regardless of the Pi's current network)."""

    def __init__(self, parent):
        self.frame = ttk.Frame(parent)
        self.status_var = tk.StringVar(value="loading...")
        ttk.Label(self.frame, textvariable=self.status_var).pack(anchor="w", padx=6, pady=4)

        hint = ("Auto-detect runs on its own. Only change Protocol/Baud/Parity here if "
                "you need to force a setting for troubleshooting (e.g. a device that "
                "won't answer Who-Is, or a non-standard baud).")
        ttk.Label(self.frame, text=hint, wraplength=900).pack(anchor="w", padx=6, pady=(0, 6))

        self.rows_frame = ttk.Frame(self.frame)
        self.rows_frame.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self._row_vars = []  # keep references so tkinter doesn't garbage-collect them

        self.refresh()

    def refresh(self):
        try:
            data = _api_get("/ports")
            self._render(data)
            self.status_var.set(f"{len(data['ports'])} port(s) known - predator-gateway API on :8099")
        except Exception as e:  # noqa: BLE001
            self.status_var.set(f"cannot reach predator-gateway API (is the service running?): {e}")
        self.frame.after(REFRESH_MS, self.refresh)

    def _render(self, data):
        for w in self.rows_frame.winfo_children():
            w.destroy()
        self._row_vars.clear()

        headers = ["Port", "Auto-detected", "Mode", "Protocol", "Baud", "Parity", ""]
        for col, text in enumerate(headers):
            ttk.Label(self.rows_frame, text=text, style="Header.TLabel").grid(
                row=0, column=col, padx=4, pady=(0, 4), sticky="w")

        protocol_choices = ["auto"] + data["protocol_choices"]
        baud_choices = [str(b) for b in data["baud_choices"]]
        parity_choices = data["parity_choices"]

        if not data["ports"]:
            ttk.Label(self.rows_frame, text="(no serial ports found - plug in a USB-RS485 adapter)").grid(
                row=1, column=0, columnspan=len(headers), sticky="w", padx=4)
            return

        for i, p in enumerate(data["ports"], start=1):
            port, state, override = p["port"], p["state"], p["override"]
            detected = state["note"] if state and state.get("note") else "-"

            ttk.Label(self.rows_frame, text=port).grid(row=i, column=0, sticky="w", padx=4, pady=2)
            ttk.Label(self.rows_frame, text=detected).grid(row=i, column=1, sticky="w", padx=4)
            ttk.Label(self.rows_frame, text="FORCED" if override else "auto").grid(row=i, column=2, sticky="w", padx=4)

            protocol_var = tk.StringVar(value=override["protocol"] if override else "auto")
            default_baud = override["baud"] if override else (state["baud"] if state and state["baud"] else data["baud_choices"][0])
            baud_var = tk.StringVar(value=str(default_baud))
            parity_var = tk.StringVar(value=override["parity"] if override else "N")

            ttk.Combobox(self.rows_frame, textvariable=protocol_var, values=protocol_choices,
                         width=14, state="readonly").grid(row=i, column=3, padx=2)
            ttk.Combobox(self.rows_frame, textvariable=baud_var, values=baud_choices,
                         width=8, state="readonly").grid(row=i, column=4, padx=2)
            ttk.Combobox(self.rows_frame, textvariable=parity_var, values=parity_choices,
                         width=4, state="readonly").grid(row=i, column=5, padx=2)
            ttk.Button(self.rows_frame, text="Apply", command=self._make_apply(port, protocol_var, baud_var, parity_var)).grid(
                row=i, column=6, padx=4)

            self._row_vars.append((protocol_var, baud_var, parity_var))

    def _make_apply(self, port, protocol_var, baud_var, parity_var):
        def _apply():
            protocol = protocol_var.get()
            try:
                if protocol == "auto":
                    _api_post("/ports/auto", {"port": port})
                else:
                    _api_post("/ports/override", {
                        "port": port, "protocol": protocol,
                        "baud": int(baud_var.get()), "parity": parity_var.get(),
                    })
            except (urllib.error.URLError, OSError, ValueError) as e:
                messagebox.showerror("predator", f"could not apply setting for {port}:\n{e}")
                return
            self.refresh()
        return _apply


def main():
    cfg = cfgmod.load_config()
    csv_path = cfgmod.csv_path(cfg)

    root = tk.Tk()
    root.title("predator gateway explorer")
    root.geometry("1100x600")
    _apply_dark_theme(root)

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True)

    discovered = DiscoveredTab(notebook, csv_path)
    ports = SerialPortsTab(notebook)
    notebook.add(discovered.frame, text="Discovered")
    notebook.add(ports.frame, text="Serial Ports")

    root.mainloop()


if __name__ == "__main__":
    main()
