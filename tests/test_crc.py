import unittest

from modbus.crc import crc16


class TestCRC16(unittest.TestCase):
    def test_known_vector_read_request(self):
        # 01 03 00 00 00 0A -> CRC CDC5 (independently verified)
        frame = bytes.fromhex("01030000000A")
        self.assertEqual(crc16(frame), 0xCDC5)

    def test_known_vector_append_order(self):
        # When appended low/high the full frame ends C5 CD.
        from modbus.rtu import encode_frame
        from modbus.pdu import encode_read_request

        frame = encode_frame(1, encode_read_request(0x03, 0, 10))
        self.assertEqual(frame.hex(), "01030000000ac5cd")

    def test_single_byte_changes(self):
        self.assertNotEqual(crc16(b"\x00"), crc16(b"\x01"))
        self.assertNotEqual(crc16(b"abc"), crc16(b"abd"))

    def test_empty(self):
        self.assertEqual(crc16(b""), 0xFFFF)


if __name__ == "__main__":
    unittest.main()
