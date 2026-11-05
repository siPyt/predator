"""MQTT: finds brokers, subscribes to everything, and flattens each message into CSV rows."""
import json
import threading
import time

from ..context import Context
from ..records import Record

PORTS = (1883, 8883)


def flatten(prefix, obj, out, depth=0):
    if isinstance(obj, dict) and depth < 4:
        for k, v in obj.items():
            flatten("%s/%s" % (prefix, k), v, out, depth + 1)
    elif isinstance(obj, list) and depth < 4 and len(obj) <= 64:
        for i, v in enumerate(obj):
            flatten("%s/%d" % (prefix, i), v, out, depth + 1)
    else:
        out.append((prefix, obj))


def message_rows(topic, payload):
    """[(point, value, extra details)] for one MQTT payload."""
    if topic.startswith(("spBv1.0/", "spAv1.0/")):
        return [(topic, payload.hex(), {"encoding": "sparkplug-b protobuf (raw)"})]
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return [(topic, payload.hex(), {"encoding": "binary"})]
    try:
        doc = json.loads(text)
    except ValueError:
        return [(topic, text, {})]
    if isinstance(doc, (dict, list)):
        rows = []
        flatten(topic, doc, rows)
        return [(p, v, {"encoding": "json"}) for p, v in rows]
    return [(topic, text, {})]


def collect(host, port, ctx: Context):
    import paho.mqtt.client as mqtt

    source = "%s:%d" % (host, port)
    state = {"connected": False, "seen": 0}
    done = threading.Event()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if ctx.mqtt_user:
        client.username_pw_set(ctx.mqtt_user, ctx.mqtt_password)
    if port == 8883:
        client.tls_set()

    def on_connect(c, _u, _f, reason, _p=None):
        if reason == 0:
            state["connected"] = True
            c.subscribe([("#", 0), ("$SYS/#", 0)])
        else:
            ctx.emit(Record("MQTT", source, "broker", "connect", "refused", details={"reason": str(reason)}))
            done.set()

    def on_message(_c, _u, msg):
        state["seen"] += 1
        for point, value, extra in message_rows(msg.topic, msg.payload):
            ctx.emit(Record("MQTT", source, "broker", point, value, details=dict(extra, retained=bool(msg.retain))))

    client.on_connect, client.on_message = on_connect, on_message
    try:
        client.connect(host, port, 30)
    except OSError as exc:
        ctx.log("mqtt %s: %s" % (source, exc))
        return
    client.loop_start()
    done.wait(ctx.mqtt_seconds)
    client.loop_stop()
    client.disconnect()
    ctx.emit(Record("MQTT", source, "broker", "summary", state["seen"], details={"connected": state["connected"]}))


def scan(ctx: Context):
    brokers = list(ctx.mqtt_brokers)
    for port in PORTS:
        for host in ctx.open_hosts(port):
            if (host, port) not in brokers:
                brokers.append((host, port))
    ctx.log("mqtt: %d broker(s)" % len(brokers))
    for host, port in brokers:
        collect(host, port, ctx)
