# Predator multi-protocol scanner

A one-shot command-line scanner (the `predator/` package) that sweeps the network and serial buses and writes
everything it finds to one CSV (`timestamp, protocol, source, device, point, value, units, details`).
It complements the always-on gateway service described in [README.md](./README.md); it does not replace it.

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
  --mqtt-broker 192.168.1.20 --mqtt-user me      # password: export PREDATOR_MQTT_PASSWORD=...
```

`--rs485-rts` drives RTS for TX direction (Modbus RTU on UART HATs without auto-direction).
`--protocols bacnet,modbus,...` limits the IP scanners (`services,bacnet,modbus,mqtt,profinet,enip,s7,opcua,knx`).

## What each scanner does

| Protocol | Method | Output |
|---|---|---|
| BACnet/IP | Who-Is (broadcast + unicast), then ReadProperty for names, values, units; follows routed MS/TP devices | device + every object |
| BACnet MS/TP | passive listen (never transmits): stations, I-Am, and every point value that other masters read or that devices send (ReadProperty/ReadPropertyMultiple ACKs, COV) | stations, devices, point values when changed |
| Modbus TCP / RTU | device identification (FC43) + coil/discrete/holding/input tables | one row per register |
| MQTT | connects to brokers (found on 1883/8883 or `--mqtt-broker`), subscribes to `#` and `$SYS/#`, flattens JSON | one row per topic/field |
| PROFINET | DCP Identify All (layer 2) | name, type, vendor/device id, IP, role |
| PROFIBUS | passive 8E1 monitor, auto-baud; stations and last raw process data | stations + raw hex |
| EtherNet/IP, KNXnet/IP, OPC UA, Siemens S7 | ListIdentity, search, hello, SZL 0x0011 | device identity |
| TCP services | probes well-known industrial ports (DNP3, IEC 104, FINS, ...) | open ports |

## Limits (be aware)

- **Serial ports are exclusive**: one protocol per RS-485 port, and not while the gateway service is using the port.
- **MS/TP and PROFIBUS are listen-only** (the Pi cannot meet their token/slot timing from Python). MS/TP only yields
  point values if a supervisor or other master is already polling/subscribing on that bus; to read points
  actively use the gateway service (bacnet-stack master) or a BACnet/IP-to-MS/TP router.
- **PROFIBUS** needs an RS-485 adapter that supports the bus baud rate (12 Mbit/s is beyond UART adapters) and a
  tap on the bus; process data meaning needs the device GSD file.
- **PROFINET** discovery is DCP only (identity); cyclic IO data is not read.
- MQTT cannot enumerate topics without subscribing; only topics published during `--mqtt-seconds` appear.
  Sparkplug B payloads are stored as raw protobuf hex.
- Tests (`python -m unittest discover -s tests`) fuzz every parser with garbage/truncated input, check the MS/TP CRCs
  against the BACnet standard, and run the BACnet/IP (incl. routed MS/TP devices), MS/TP, PROFIBUS, Modbus RTU/TCP
  and MQTT scanners end to end against simulated devices. Nothing has been verified on real hardware yet, so run
  a first scan against a known device and compare the CSV with the vendor tool before relying on it.
