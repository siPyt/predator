import time


def open_serial(port, baud, parity="N", timeout=0.2):
    import serial

    return serial.Serial(
        port,
        baudrate=baud,
        bytesize=8,
        parity={"N": serial.PARITY_NONE, "E": serial.PARITY_EVEN, "O": serial.PARITY_ODD}[parity],
        stopbits=1,
        timeout=timeout,
    )


def sniff(ser, seconds, on_bytes):
    """Read for `seconds`, passing each chunk to on_bytes; stops early if it returns True."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        chunk = ser.read(512)
        if chunk and on_bytes(chunk):
            return True
    return False
