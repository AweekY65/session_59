import unittest

from modbus import pdu
from modbus.device import DeviceMap, ModbusDevice, WordOrder, load_devices
from modbus.pdu import (
    FC_READ_HOLDING_REGISTERS as FC03,
    FC_READ_INPUT_REGISTERS as FC04,
    FC_WRITE_SINGLE_REGISTER as FC06,
    FC_WRITE_MULTIPLE_REGISTERS as FC10,
)


def _exc(pdu_bytes: bytes) -> int:
    """Pull the exception code out of an exception PDU."""
    assert pdu_bytes[0] & 0x80
    return pdu_bytes[1]


class TestPDUCodec(unittest.TestCase):
    def test_read_request_roundtrip(self):
        for fc in (FC03, FC04):
            frame = pdu.encode_read_request(fc, 0x1234, 7)
            self.assertEqual(frame, bytes([fc, 0x12, 0x34, 0x00, 0x07]))
            self.assertEqual(pdu.decode_read_request(frame), (fc, 0x1234, 7))

    def test_read_response_roundtrip(self):
        frame = pdu.encode_read_response(FC03, [0x1234, 0x5678])
        self.assertEqual(frame, bytes.fromhex("030412345678"))
        self.assertEqual(pdu.decode_read_response(frame), (FC03, [0x1234, 0x5678]))

    def test_write_single_roundtrip(self):
        frame = pdu.encode_write_single_request(5, 0xABCD)
        self.assertEqual(frame, bytes.fromhex("060005abcd"))
        self.assertEqual(pdu.decode_write_single_request(frame), (5, 0xABCD))

    def test_write_multiple_roundtrip(self):
        frame = pdu.encode_write_multiple_request(8, [1, 2, 3])
        self.assertEqual(frame, bytes.fromhex("100008000306000100020003"))
        address, values = pdu.decode_write_multiple_request(frame)
        self.assertEqual((address, values), (8, [1, 2, 3]))
        self.assertEqual(
            pdu.encode_write_multiple_response(8, 3), bytes.fromhex("1000080003")
        )

    def test_malformed_lengths_rejected(self):
        with self.assertRaises(pdu.IllegalDataValue):
            pdu.decode_read_request(b"\x03\x00\x00\x00")
        with self.assertRaises(pdu.IllegalDataValue):
            pdu.decode_read_response(b"\x03\x03\x00\x00")  # odd byte count
        with self.assertRaises(pdu.IllegalDataValue):
            pdu.decode_write_single_request(b"\x06\x00\x00\xff")
        with self.assertRaises(pdu.IllegalDataValue):
            pdu.decode_write_multiple_request(b"\x10\x00\x00\x00\x01\x04\x00\x00")
        with self.assertRaises(pdu.IllegalFunction):
            pdu.decode_read_request(b"\x05\x00\x00\x00\x01")

    def test_quantity_limits(self):
        pdu.encode_read_request(FC03, 0, 125)
        with self.assertRaises(pdu.IllegalDataValue):
            pdu.encode_read_request(FC03, 0, 126)
        with self.assertRaises(pdu.IllegalDataValue):
            pdu.encode_read_request(FC03, 0, 0)
        pdu.encode_write_multiple_request(0, [0] * 123)
        with self.assertRaises(pdu.IllegalDataValue):
            pdu.encode_write_multiple_request(0, [0] * 124)


class TestDevice(unittest.TestCase):
    def setUp(self):
        self.dev = ModbusDevice(1, size=100, input_size=50)

    def test_holding_read_write(self):
        self.dev.write_multiple(0, [10, 20, 30])
        self.assertEqual(self.dev.read_holding(0, 3), [10, 20, 30])
        self.dev.write_single(2, 99)
        self.assertEqual(self.dev.read_holding(0, 3), [10, 20, 99])
        # Unwritten addresses read as zero.
        self.assertEqual(self.dev.read_holding(50, 2), [0, 0])

    def test_input_is_read_only(self):
        # Input bank is separate from holding and never altered by writes.
        self.dev._input[0] = 7
        self.assertEqual(self.dev.read_input(0, 1), [7])
        self.dev.write_single(0, 1234)
        self.assertEqual(self.dev.read_input(0, 1), [7])

    def test_boundary_addresses_holding(self):
        # Last legal range in a 100-register bank.
        self.dev.write_multiple(98, [0xAAAA, 0xBBBB])
        self.assertEqual(self.dev.read_holding(98, 2), [0xAAAA, 0xBBBB])

    def test_address_out_of_range(self):
        with self.assertRaises(pdu.IllegalDataAddress):
            self.dev.read_holding(100, 1)
        with self.assertRaises(pdu.IllegalDataAddress):
            self.dev.read_holding(99, 2)
        with self.assertRaises(pdu.IllegalDataAddress):
            self.dev.write_single(100, 1)
        with self.assertRaises(pdu.IllegalDataAddress):
            self.dev.read_input(50, 1)

    # -- 32-bit / word order -------------------------------------------------
    def test_u32_high_word_first(self):
        dev = ModbusDevice(1, word_order=WordOrder.HIGH_WORD_FIRST)
        dev.write_u32(0, 0x12345678)
        self.assertEqual(dev.read_holding(0, 2), [0x1234, 0x5678])
        self.assertEqual(dev.read_u32(0), 0x12345678)

    def test_u32_low_word_first(self):
        dev = ModbusDevice(1, word_order=WordOrder.LOW_WORD_FIRST)
        dev.write_u32(0, 0x12345678)
        self.assertEqual(dev.read_holding(0, 2), [0x5678, 0x1234])
        self.assertEqual(dev.read_u32(0), 0x12345678)

    def test_word_order_string_parse_and_override(self):
        dev = ModbusDevice(1, word_order="abcd")
        dev.write_u32(0, 0x12345678)
        self.assertEqual(dev.read_u32(0, "dcba"), 0x56781234)

    def test_signed_32(self):
        dev = ModbusDevice(1)
        dev.write_i32(0, -1)
        self.assertEqual(dev.read_u32(0), 0xFFFFFFFF)
        self.assertEqual(dev.read_i32(0), -1)
        dev.write_i32(2, -2147483648)
        self.assertEqual(dev.read_i32(2), -2147483648)

    # -- PDU dispatch / exception codes -------------------------------------
    def test_handle_read_write_pdus(self):
        self.assertEqual(
            self.dev.handle_pdu(pdu.encode_write_single_request(5, 42)),
            bytes.fromhex("060005002a"),
        )
        response = self.dev.handle_pdu(pdu.encode_read_request(FC03, 5, 1))
        self.assertEqual(pdu.decode_read_response(response), (FC03, [42]))

        self.dev._input[7] = 9
        response = self.dev.handle_pdu(pdu.encode_read_request(FC04, 7, 1))
        self.assertEqual(pdu.decode_read_response(response), (FC04, [9]))

        response = self.dev.handle_pdu(
            pdu.encode_write_multiple_request(10, [11, 12])
        )
        self.assertEqual(response, bytes.fromhex("10000a0002"))

    def test_exception_illegal_function(self):
        response = self.dev.handle_pdu(b"\x05\x00\x00\x00\x01")
        self.assertEqual(response[0], 0x85)
        self.assertEqual(_exc(response), pdu.EXN_ILLEGAL_FUNCTION)

    def test_exception_illegal_data_address(self):
        response = self.dev.handle_pdu(pdu.encode_read_request(FC03, 99, 2))
        self.assertEqual(response[0], 0x83)
        self.assertEqual(_exc(response), pdu.EXN_ILLEGAL_DATA_ADDRESS)

        response = self.dev.handle_pdu(pdu.encode_write_single_request(100, 1))
        self.assertEqual(_exc(response), pdu.EXN_ILLEGAL_DATA_ADDRESS)

        response = self.dev.handle_pdu(
            pdu.encode_write_multiple_request(99, [1, 2])
        )
        self.assertEqual(_exc(response), pdu.EXN_ILLEGAL_DATA_ADDRESS)

        # FC04 has its own (smaller) input bank.
        response = self.dev.handle_pdu(pdu.encode_read_request(FC04, 49, 2))
        self.assertEqual(_exc(response), pdu.EXN_ILLEGAL_DATA_ADDRESS)

    def test_exception_illegal_data_value(self):
        # quantity 126 on read -> 03 Illegal Data Value
        response = self.dev.handle_pdu(
            bytes([FC03]) + (0).to_bytes(2, "big") + (126).to_bytes(2, "big")
        )
        self.assertEqual(_exc(response), pdu.EXN_ILLEGAL_DATA_VALUE)
        # truncated frame
        self.assertEqual(
            _exc(self.dev.handle_pdu(b"\x03\x00")), pdu.EXN_ILLEGAL_DATA_VALUE
        )


class TestDeviceMapIsolation(unittest.TestCase):
    def setUp(self):
        self.dev1 = ModbusDevice(1, size=10)
        self.dev2 = ModbusDevice(2, size=10)
        self.dev1.write_single(0, 111)
        self.dev2.write_single(0, 222)
        self.map = DeviceMap([self.dev1, self.dev2])

    def test_units_are_isolated(self):
        self.assertEqual(self.map.get(1).read_holding(0, 1), [111])
        self.assertEqual(self.map.get(2).read_holding(0, 1), [222])
        with self.assertRaises(pdu.GatewayTargetFailed):
            self.map.get(9)

    def test_unknown_unit_tcp_exception(self):
        response = self.map.handle(9, pdu.encode_read_request(FC03, 0, 1))
        self.assertEqual(response[0], 0x83)
        self.assertEqual(_exc(response), pdu.EXN_GATEWAY_TARGET_FAILED)

    def test_unknown_unit_cannot_reach_other_data(self):
        # The exception must never contain register data from units 1/2.
        bad = self.map.handle(9, pdu.encode_read_request(FC03, 0, 1))
        self.assertEqual(len(bad), 2)
        self.assertNotIn(bad, (
            self.map.handle(1, pdu.encode_read_request(FC03, 0, 1)),
        ))

    def test_load_devices_from_json(self):
        import json
        import tempfile
        from pathlib import Path

        config = {
            "devices": [
                {
                    "unit_id": 1,
                    "size": 100,
                    "word_order": "dcba",
                    "holding": {"0": [1, 2, 3], "10": 99},
                    "input": {"4": [55]},
                },
                {"unit_id": 2, "size": 10, "holding": {"0": 42}},
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "devices.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            device_map = load_devices(path)

        self.assertEqual(device_map.unit_ids(), [1, 2])
        self.assertEqual(device_map.get(1).read_holding(0, 3), [1, 2, 3])
        self.assertEqual(device_map.get(1).read_holding(10, 1), [99])
        self.assertEqual(device_map.get(1).read_input(4, 1), [55])
        self.assertEqual(device_map.get(1).word_order, WordOrder.LOW_WORD_FIRST)
        self.assertEqual(device_map.get(2).read_holding(0, 1), [42])


if __name__ == "__main__":
    unittest.main()
