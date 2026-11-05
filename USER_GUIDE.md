# Predator Gateway — User Guide

Predator is a self-contained gateway box: plug in your field wiring, power it
on, and it figures out the protocol and speaks BACnet/IP, BACnet MS/TP, and
Modbus RTU to each other automatically — no configuration needed for normal
use.

This guide covers day-to-day use. For the technical/developer side (code
layout, install steps, how the auto-detect engine works), see
[README.md](./README.md).

## 1. Quick start

1. **Plug in your field wiring**, either or both:
   - **RJ45 (Ethernet)** → for a BACnet/IP network.
   - **USB-C-to-RS485 adapter** → for BACnet MS/TP *or* Modbus RTU (you don't
     need to tell it which one — it figures that out on its own).
2. **Press the power button** on the Pi.
3. **Wait.** Predator starts scanning automatically in the background —
   typically a few minutes if something's really there to find, longer if
   it has to sweep through every possibility with nothing responding.
4. **Check the results** any time via the Explorer app (see below) or the
   discovered-points CSV.

You do not need to log in, run any command, or tell Predator what's plugged
in. It is designed to run completely unattended.

## 2. Seeing what it found

### The Explorer app (on Predator's own screen, via VNC or a monitor)

Look for the teal panther icon in the top panel (next to the web browser,
file manager, and terminal icons). Click it to open **Predator Gateway
Explorer**. If you ever close the window, that same icon reopens it — it
doesn't run away.

Two tabs:
- **Discovered** — a live table of everything found: BACnet MS/TP objects
  (device + object type/instance + name), Modbus RTU units, and BACnet/IP
  devices. Updates automatically every few seconds.
- **Serial Ports** — shows what auto-detect currently believes is on each
  serial port. Normally you leave this alone. If a device isn't being found
  (see Troubleshooting below), you can manually force a Protocol/Baud/Parity
  here and hit **Apply** — or set it back to **auto** to let Predator go back
  to figuring it out on its own.

### The CSV file directly

The same data is also written continuously to a CSV file:
- On Predator's own desktop: `predator_discovered.csv`
- Open it with **Gnumeric** (right-click → Open With) for a proper
  spreadsheet view, or the Explorer app's Discovered tab.

### Downloading the CSV to another PC

With that PC on the same network as Predator (see Section 3), open a browser
and go to:

```
http://192.168.1.50:8099/export.csv
```

That downloads the current CSV directly - no VNC or file transfer needed.
`http://192.168.1.50:8099/` also has a couple of other read-only status
pages if you're curious.

## 3. Connecting to Predator from a PC

Predator's Ethernet is a fixed address: **192.168.1.50**. This only works if
your PC's Ethernet adapter is on the same network. For a direct
PC-to-Predator cable (no router in between), give your PC's Ethernet adapter
a static IP in the same range, e.g. `192.168.1.101/24`, then:

- **VNC** (to see Predator's desktop/Explorer app remotely): connect to
  `192.168.1.50` in TigerVNC (or `192.168.1.50::5900` if that doesn't work),
  then enter the Pi's login password when prompted.
- **Browser**: `http://192.168.1.50:8099/` for status/CSV download (see
  above) - no password needed for this part.

If Predator is on a real network with a router/DHCP instead of a direct
cable, it'll need its network switched back to automatic addressing first -
ask for that if it's not already been set up that way.

## 4. Using the results to set up a BMS (e.g. Insight/Apogee)

The Discovered CSV's `address` and `object_or_register` columns are exactly
the BACnet addressing info (device instance + object type:instance) you need
to configure a point in a BMS. The `description` column carries the object's
real name (e.g. "Space Temp") where available, to help you tell points apart.

Predator itself also exposes a BACnet/IP virtual device (so a BMS that reads
BACnet/IP can talk to whatever you've curated into Predator's own points
map) — ask if you want help wiring specific points through rather than just
using the CSV as a reference/inventory.

## 5. Troubleshooting

**Nothing shows up in Discovered after several minutes:**
- Check the physical wiring (A/B polarity, termination, ground) on the
  RS-485 side.
- Open the Serial Ports tab and check the port is listed at all - if it's
  not, the USB-RS485 adapter itself may not be detected; try a different
  USB port/cable.
- If you *know* the correct protocol/baud (e.g. from a datasheet) and
  auto-detect hasn't found it, use the Serial Ports tab to force it
  manually and hit Apply.

**Explorer app won't open / the panel icon is missing:**
- The panel icon survives a reboot - if it's genuinely missing, something
  deeper changed; don't try to fix the panel yourself over SSH (killing the
  wrong process can take down the whole top panel) - ask for help instead.

**The Pi's clock looks wrong:**
- If a battery is installed on the Pi 5's onboard RTC connector, the time
  should hold across power-offs. If it's resetting to 1970 or similar, the
  battery may be missing, dead, or not seated correctly.

**Network/VNC/SSH not reachable at all:**
- See `sofar.txt` in the predator repo for the full history of network
  troubleshooting done on this specific unit, including exact fixes applied.

## 6. Reference

| Item | Value |
|---|---|
| Predator's static IP | `192.168.1.50` |
| Status/CSV API | `http://192.168.1.50:8099/` |
| CSV on Predator's desktop | `~/Desktop/predator_discovered.csv` |
| BACnet/IP port | `47808` (standard) |
| systemd service name | `predator-gateway` |
