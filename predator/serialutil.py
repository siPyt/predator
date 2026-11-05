import time


def open_serial(port, baud, parity="N", timeout=0.2, rs485=False):
    """Opens a device path or a pyserial URL (socket://, loop://); rs485 drives RTS for TX direction."""
    import serial

    ser = serial.serial_for_url(
        port,
        baudrate=baud,
        bytesize=8,
        parity={"N": serial.PARITY_NONE, "E": serial.PARITY_EVEN, "O": serial.PARITY_ODD}[parity],
        stopbits=1,
        timeout=timeout,
    )
    if rs485:
        import serial.rs485

        ser.rs485_mode = serial.rs485.RS485Settings(rts_level_for_tx=True, rts_level_for_rx=False)
    return ser


def sniff(ser, seconds, on_bytes):
    """Read for `seconds`, passing each chunk to on_bytes; stops early if it returns True."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        chunk = ser.read(512)
        if chunk and on_bytes(chunk):
            return True
    return False
