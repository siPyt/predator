from . import bacnet, modbus, mqtt, others, profibus, profinet, siemens_p1

# name -> scanner(ctx) for IP / layer-2 protocols
IP_SCANNERS = {
    "services": others.scan_services,
    "bacnet": bacnet.scan,
    "modbus": modbus.scan,
    "mqtt": mqtt.scan,
    "profinet": profinet.scan,
    "enip": others.scan_enip,
    "s7": others.scan_s7,
    "opcua": others.scan_opcua,
    "knx": others.scan_knx,
}

# serial protocol name -> scanner(ctx, SerialSpec)
SERIAL_SCANNERS = {
    "modbus-rtu": modbus.scan_serial,
    "bacnet-mstp": bacnet.scan_serial,
    "profibus": profibus.scan_serial,
    "siemens-p1": siemens_p1.scan_serial,
}
