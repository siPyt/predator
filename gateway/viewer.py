"""A small always-running window that shows predator's discovered-points CSV
as a live table, so it's visible on the Pi's own desktop over VNC without
needing any other software installed (standard Raspberry Pi OS Desktop does
not ship a spreadsheet app - see README - but Tkinter ships with python3 by
itself, so this has no extra dependency).

Not part of the predator-gateway systemd service: this is a GUI program, so
it's started by the desktop session itself (see the XDG autostart entry
installed by scripts/install.sh), the same way any other login-time app
would be, and simply re-reads the CSV file on a timer.
"""

import csv
import os
import tkinter as tk
from tkinter import ttk

from . import config as cfgmod

REFRESH_MS = 3000


class ViewerApp:
    def __init__(self, root, csv_path):
        self.root = root
        self.csv_path = csv_path
        root.title("predator - discovered points")
        root.geometry("1000x500")

        top = ttk.Frame(root)
        top.pack(fill="x", padx=6, pady=4)
        self.status_var = tk.StringVar(value="starting...")
        ttk.Label(top, textvariable=self.status_var).pack(side="left")

        columns = ("source", "bus_or_network", "address", "object_or_register",
                   "data_type", "description", "value", "units", "writable", "last_seen")
        self.tree = ttk.Treeview(root, columns=columns, show="headings")
        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=100, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        scroll = ttk.Scrollbar(root, orient="vertical", command=self.tree.yview)
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
        self.root.after(REFRESH_MS, self.refresh)


def main():
    cfg = cfgmod.load_config()
    csv_path = cfgmod.csv_path(cfg)
    root = tk.Tk()
    ViewerApp(root, csv_path)
    root.mainloop()


if __name__ == "__main__":
    main()
