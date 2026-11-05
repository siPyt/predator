# Predator

Scans field buses and writes everything it finds to one CSV
(`timestamp, protocol, source, device, point, value, units, details`).

## Install (on the Pi)

```bash
git clone https://github.com/siPyt/predator.git && cd predator
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
```

## Run

```bash
# IP side: auto-detects the local subnet. PROFINET DCP needs raw Ethernet, so use sudo (keep the venv python).
sudo .venv/bin/predator -o scan.csv

# add serial buses (one --serial per RS-485 port; port:protocol[:bauds][:parity])
sudo .venv/bin/predator -o scan.csv \
  --serial /dev/ttyAMA2:modbus-rtu:9600,19200 \
  --serial /dev/ttyAMA3:bacnet-mstp \
  --serial /dev/ttyUSB0:profibus \
  --serial /dev/ttyUSB1:siemens-p1 \
  --mqtt-broker 192.168.1.20 --mqtt-user me      # password: export PREDATOR_MQTT_PASSWORD=...
```

`--protocols bacnet,modbus,...` limits the IP scanners (`services,bacnet,modbus,mqtt,profinet,enip,s7,opcua,knx`).

## What each scanner does

| Protocol | Method | Output |
|---|---|---|
| BACnet/IP | Who-Is (broadcast + unicast), then ReadProperty for names, values, units; follows routed MS/TP devices | device + every object |
| BACnet MS/TP | passive listen (never transmits): stations, I-Am | stations/devices |
| Modbus TCP / RTU | device identification (FC43) + coil/discrete/holding/input tables | one row per register |
| MQTT | connects to brokers (found on 1883/8883 or `--mqtt-broker`), subscribes to `#` and `$SYS/#`, flattens JSON | one row per topic/field |
| PROFINET | DCP Identify All (layer 2) | name, type, vendor/device id, IP, role |
| PROFIBUS | passive 8E1 monitor, auto-baud; stations and last raw process data | stations + raw hex |
| Siemens P1 (FLN) | passive capture at 4800/9600/... baud | raw frames (hex + text) |
| EtherNet/IP, KNXnet/IP, OPC UA, Siemens S7 | ListIdentity, search, hello, SZL 0x0011 | device identity |
| TCP services | probes well-known industrial ports (DNP3, IEC 104, FINS, ...) | open ports |

## Limits (be aware)

- **Serial ports are exclusive**: one protocol per RS-485 port. MS/TP, PROFIBUS and P1 are listen-only because
  the Pi cannot meet their token/slot timing from Python.
- **Siemens P1 is not decoded yet.** The frame layout is not public; frames are captured raw. Provide the spec or a
  capture and add a decoder in `predator/scanners/siemens_p1.py:decode_line`. Siemens Desigo/PXC controllers
  that also speak BACnet are fully covered by the BACnet scanner.
- **PROFIBUS** needs an RS-485 adapter that supports the bus baud rate (12 Mbit/s is beyond UART adapters) and a
  tap on the bus; process data meaning needs the device GSD file.
- **PROFINET** discovery is DCP only (identity); cyclic IO data is not read.
- MQTT cannot enumerate topics without subscribing; only topics published during `--mqtt-seconds` appear.
  Sparkplug B payloads are stored as raw protobuf hex.
- Unit tests cover the parsers and framing (`python -m unittest discover -s tests`); only Modbus TCP has been
  exercised against a (simulated) device, the rest need real hardware for verification.
