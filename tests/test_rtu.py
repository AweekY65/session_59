import socket
import unittest

from modbus import pdu
from modbus.crc import crc16
from modbus.device import DeviceMap, ModbusDevice
from modbus.rtu import (
    CRCError,
    RTUFramer,
    RTUServer,
    decode_frame,
    encode_frame,
    serve_rtu_connection,
)


def _request(unit, fc_pdu: bytes) -> bytes:
    return encode_frame(unit, fc_pdu)


class TestRTUCodec(unittest.TestCase):
    def test_frame_roundtrip(self):
        request = pdu.encode_read_request(0x03, 0x0010, 2)
        frame = encode_frame(1, request)
        unit, decoded_pdu = decode_frame(frame)
        self.assertEqual(unit, 1)
        self.assertEqual(decoded_pdu, request)
        # CRC appended low byte then high byte.
        crc = crc16(frame[:-2])
        self.assertEqual(frame[-2], crc & 0xFF)
        self.assertEqual(frame[-1], crc >> 8)

    def test_bad_crc_raises(self):
        frame = bytearray(encode_frame(1, pdu.encode_read_request(0x03, 0, 1)))
        frame[3] ^= 0xFF
        with self.assertRaises(CRCError):
            decode_frame(frame)

    def test_runt_frame(self):
        with self.assertRaises(ValueError):
            decode_frame(b"\x01\x03")


class TestRTUFramer(unittest.TestCase):
    def test_half_then_rest(self):
        frame = encode_frame(1, pdu.encode_read_request(0x03, 0, 3))
        framer = RTUFramer(role="master")
        self.assertEqual(framer.feed(frame[:3]), [])
        self.assertEqual(framer.feed(frame[3:]), [frame])

    def test_coalesced_frames(self):
        f1 = encode_frame(1, pdu.encode_read_request(0x03, 0, 1))
        f2 = encode_frame(2, pdu.encode_write_single_request(4, 0xBEEF))
        f3 = encode_frame(1, pdu.encode_write_multiple_request(8, [1, 2]))
        blob = f1 + f2 + f3
        framer = RTUFramer(role="master")
        self.assertEqual(framer.feed(blob), [f1, f2, f3])

    def test_byte_by_byte(self):
        frame = encode_frame(1, pdu.encode_write_multiple_request(0, [5, 6]))
        framer = RTUFramer(role="master")
        out = []
        for b in frame:
            out += framer.feed(bytes([b]))
        self.assertEqual(out, [frame])

    def test_slave_side_response_framing(self):
        # Variable-length read response via byte-count byte.
        request_pdu = pdu.encode_read_request(0x03, 0, 2)
        response_pdu = pdu.encode_read_response(0x03, [0xA, 0xB])
        response = encode_frame(1, response_pdu)
        framer = RTUFramer(role="slave")
        self.assertEqual(framer.feed(response), [response])
        # exception responses are fixed 5 bytes
        exc = encode_frame(1, pdu.exception_response(0x03, 0x02))
        self.assertEqual(RTUFramer(role="slave").feed(exc), [exc])

    def test_garbage_delivered_on_flush(self):
        framer = RTUFramer(role="master")
        junk = b"\x01\x66\x00\x00\xab\xcd"  # unknown fc
        self.assertEqual(framer.feed(junk), [])
        self.assertEqual(framer.flush(), [junk])
        with self.assertRaises(CRCError):
            decode_frame(junk)


def _client_exchange(sock: socket.socket, frame: bytes, expected_len: int = 8) -> bytes:
    sock.sendall(frame)
    sock.settimeout(2)
    data = b""
    while len(data) < expected_len:
        chunk = sock.recv(expected_len - len(data))
        if not chunk:
            break
        data += chunk
    return data


class TestRTUServer(unittest.TestCase):
    def setUp(self):
        dev1 = ModbusDevice(1, size=100)
        dev2 = ModbusDevice(2, size=100)
        dev1.write_single(0, 1234)
        dev2.write_single(0, 5678)
        self.dev1, self.dev2 = dev1, dev2
        self.map = DeviceMap([dev1, dev2])
        self.server = RTUServer(self.map, crc_policy="respond")
        self.thread = self.server.serve_forever_in_thread()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_end_to_end_read_write_multiple_units(self):
        with socket.create_connection(("127.0.0.1", self.server.port), timeout=2) as s:
            # unit 1 read
            req = encode_frame(1, pdu.encode_read_request(0x03, 0, 1))
            reply = _client_exchange(s, req, expected_len=7)
            unit, resp = decode_frame(reply)
            self.assertEqual(unit, 1)
            self.assertEqual(pdu.decode_read_response(resp), (0x03, [1234]))

            # unit 2 read -> isolated value
            req = encode_frame(2, pdu.encode_read_request(0x03, 0, 1))
            reply = _client_exchange(s, req, expected_len=7)
            unit, resp = decode_frame(reply)
            self.assertEqual(pdu.decode_read_response(resp), (0x03, [5678]))

            # write multiple unit 1, unit 2 unaffected
            req = encode_frame(1, pdu.encode_write_multiple_request(10, [42, 43]))
            reply = _client_exchange(s, req, expected_len=8)
            unit, resp = decode_frame(reply)
            self.assertEqual(pdu.decode_write_multiple_response(resp), (10, 2))
            self.assertEqual(self.dev1.read_holding(10, 2), [42, 43])
            self.assertEqual(self.dev2.read_holding(10, 2), [0, 0])

    def test_exception_illegal_function_and_address(self):
        with socket.create_connection(("127.0.0.1", self.server.port), timeout=2) as s:
            bad_fc = encode_frame(1, b"\x05\x00\x00\x00\x01")
            reply = _client_exchange(s, bad_fc, expected_len=5)
            _, resp = decode_frame(reply)
            self.assertEqual(resp, pdu.exception_response(0x05, pdu.EXN_ILLEGAL_FUNCTION))

            bad_addr = encode_frame(1, pdu.encode_read_request(0x03, 99, 5))
            reply = _client_exchange(s, bad_addr, expected_len=5)
            _, resp = decode_frame(reply)
            self.assertEqual(resp, pdu.exception_response(0x03, pdu.EXN_ILLEGAL_DATA_ADDRESS))

    def test_bad_crc_responds_exception(self):
        with socket.create_connection(("127.0.0.1", self.server.port), timeout=2) as s:
            bad = bytearray(encode_frame(1, pdu.encode_read_request(0x03, 0, 1)))
            bad[-1] ^= 0xFF  # corrupt CRC
            s.sendall(bad)
            reply = _client_exchange(s, b"", expected_len=5)
            # Send nothing new; the reply was triggered by the bad frame.
            unit, resp = decode_frame(reply)
            self.assertEqual(resp[0], 0x83)
            self.assertEqual(resp[1], pdu.EXN_SLAVE_DEVICE_FAILURE)

    def test_bad_crc_drop_policy(self):
        server = RTUServer(self.map, crc_policy="drop")
        thread = server.serve_forever_in_thread()
        try:
            with socket.create_connection(("127.0.0.1", server.port), timeout=2) as s:
                bad = bytearray(encode_frame(1, pdu.encode_read_request(0x03, 0, 1)))
                bad[-1] ^= 0xFF
                s.sendall(bad)
                s.settimeout(0.5)
                with self.assertRaises(socket.timeout):
                    s.recv(64)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_unknown_unit_is_silent(self):
        with socket.create_connection(("127.0.0.1", self.server.port), timeout=2) as s:
            req = encode_frame(9, pdu.encode_read_request(0x03, 0, 1))
            s.sendall(req)
            s.settimeout(0.5)
            with self.assertRaises(socket.timeout):
                s.recv(64)

    def test_fragmented_and_coalesced_rtu_over_socket(self):
        with socket.create_connection(("127.0.0.1", self.server.port), timeout=2) as s:
            f1 = encode_frame(1, pdu.encode_read_request(0x03, 0, 1))
            f2 = encode_frame(2, pdu.encode_read_request(0x03, 0, 1))
            # half of f1, the other half glued to f2
            s.sendall(f1[:4])
            s.sendall(f1[4:] + f2)
            s.settimeout(2)
            replies = b""
            while len(replies) < 14:
                chunk = s.recv(64)
                replies += chunk
            _, r1 = decode_frame(replies[:7])
            _, r2 = decode_frame(replies[7:14])
            self.assertEqual(pdu.decode_read_response(r1), (0x03, [1234]))
            self.assertEqual(pdu.decode_read_response(r2), (0x03, [5678]))


if __name__ == "__main__":
    unittest.main()
