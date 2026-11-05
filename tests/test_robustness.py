import random
import unittest

from predator.scanners import bacnet, mqtt, others, profibus, profinet

PARSERS = [
    bacnet.parse_mstp,
    bacnet.parse_npdu,
    bacnet.parse_iam,
    bacnet.decode_apdu_points,
    lambda d: bacnet.parse_read_ack(d, 1),
    profibus.parse_telegrams,
    profinet.parse_identify_response,
    others.parse_list_identity,
    others.parse_knx_search_response,
    others.parse_s7_order_number,
    lambda d: mqtt.message_rows("a/b", d),
]

SEEDS = [
    b"\x55\xff\x06\xff\x05\x00\x04\x00",
    b"\x30\x07\x0c\x0c\x00\x00\x00\x03\x19\x55\x3e\x44\x41\xac\x00\x00\x3f",
    b"\x10\x03\x82\x49\xce\x16",
    b"\x68\x05\x05\x68\x03\x02\x7d\x01\x02\x8b\x16",
    b"\x01\x20\xff\xff\x00\xff\x10\x00\xc4\x02\x00\x04\xd2\x22\x05\xc4\x91\x03\x21\x07",
    b"\x06\x10\x02\x02\x00\x36" + bytes(60),
]


class FuzzTests(unittest.TestCase):
    """Noise on a serial bus or hostile packets must never raise from a parser."""

    def test_random_and_mutated_input_never_raises(self):
        rng = random.Random(1234)
        for parse in PARSERS:
            for _ in range(3000):
                data = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 80)))
                parse(data)
            for seed in SEEDS:
                for _ in range(1500):
                    buf = bytearray(seed)
                    for _ in range(rng.randrange(1, 4)):
                        buf[rng.randrange(len(buf))] = rng.randrange(256)
                    cut = rng.randrange(len(buf) + 1)
                    parse(bytes(buf[:cut]))

    def test_known_mstp_crc_vector(self):
        # Annex G example: accumulator 0x73, transmitted (ones' complement) 0x8C
        self.assertEqual(bacnet.mstp_header_crc(bytes([0, 0x10, 0x05, 0, 0])), 0x8C)

    def test_data_crc_is_crc16_x25(self):
        def x25(data):
            crc = 0xFFFF
            for b in data:
                crc ^= b
                for _ in range(8):
                    crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
            return ~crc & 0xFFFF

        rng = random.Random(7)
        for _ in range(500):
            d = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 60)))
            self.assertEqual(bacnet.mstp_data_crc(d), x25(d))


if __name__ == "__main__":
    unittest.main()
