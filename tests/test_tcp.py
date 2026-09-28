import socket
import struct
import unittest

from modbus import pdu
from modbus.device import DeviceMap, ModbusDevice, WordOrder
from modbus.tcp import TCPFramer, TCPServer, decode_mbap, encode_mbap


def make_map():
    dev1 = ModbusDevice(1, size=100, word_order=WordOrder.HIGH_WORD_FIRST)
    dev2 = ModbusDevice(2, size=100, word_order=WordOrder.LOW_WORD_FIRST)
    dev1.write_multiple(0, [100, 200])
    dev1.write_u32(10, 0x12345678)
    dev2.write_multiple(0, [300, 400])
    dev2.write_u32(10, 0x12345678)
    return DeviceMap([dev1, dev2]), dev1, dev2


def recv_exactly(sock: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("closed before %d bytes" % n)
        data += chunk
    return data


def read_one_adu(sock: socket.socket) -> tuple[int, int, bytes]:
    header = recv_exactly(sock, 7)
    _tid, _pid, length, uid = struct.unpack(">HHH B", header)
    payload = recv_exactly(sock, length - 1)
    return _tid, uid, payload


class TestMBAPCodec(unittest.TestCase):
    def test_roundtrip(self):
        adu = encode_mbap(0x1234, 5, b"\x03\x00\x00\x00\x01")
        self.assertEqual(adu.hex(), "123400000006050300000001")
        tid, unit, data = decode_mbap(adu)
        self.assertEqual((tid, unit, data), (0x1234, 5, b"\x03\x00\x00\x00\x01"))

    def test_bad_protocol_and_length(self):
        adu = bytearray(encode_mbap(1, 1, b"\x03\x00\x00\x00\x01"))
        adu[2] = 1  # protocol id = 1
        with self.assertRaises(ValueError):
            decode_mbap(adu)
        adu = bytearray(encode_mbap(1, 1, b"\x03\x00\x00\x00\x01"))
        adu[5] = 9  # claim 9 bytes but only 6 present
        with self.assertRaises(ValueError):
            decode_mbap(adu)
        with self.assertRaises(ValueError):
            decode_mbap(b"\x00\x01")


class TestTCPFramer(unittest.TestCase):
    def _adu(self, tid, uid, data):
        return encode_mbap(tid, uid, data)

    def test_half_packet_reassembly(self):
        adu = self._adu(1, 1, b"\x03\x00\x00\x00\x02")
        framer = TCPFramer()
        self.assertEqual(framer.feed(adu[:2]), [])
        self.assertEqual(framer.feed(adu[2:6]), [])
        self.assertEqual(framer.buffered(), 6)
        self.assertEqual(framer.feed(adu[6:]), [adu])
        self.assertEqual(framer.buffered(), 0)

    def test_coalesced_packets(self):
        a1 = self._adu(1, 1, b"\x03\x00\x00\x00\x01")
        a2 = self._adu(2, 2, b"\x06\x00\x00\x12\x34")
        a3 = self._adu(3, 1, b"\x10\x00\x00\x00\x01\x02\xab\xcd")
        framer = TCPFramer()
        self.assertEqual(framer.feed(a1 + a2 + a3), [a1, a2, a3])

    def test_byte_by_byte(self):
        adu = self._adu(7, 3, b"\x03\x00\x00\x00\x03")
        framer = TCPFramer()
        out = []
        for b in adu:
            out += framer.feed(bytes([b]))
        self.assertEqual(out, [adu])

    def test_split_at_every_byte_boundary(self):
        # Exhaustive split: every cut point of two glued frames.
        a1 = self._adu(1, 1, b"\x03\x00\x00\x00\x01")
        a2 = self._adu(2, 1, b"\x06\x00\x05\x00\x2a")
        blob = a1 + a2
        for cut in range(1, len(blob)):
            framer = TCPFramer()
            frames = framer.feed(blob[:cut])
            frames += framer.feed(blob[cut:])
            self.assertEqual(frames, [a1, a2], f"failed at cut {cut}")

    def test_invalid_length_resets(self):
        framer = TCPFramer()
        bad = struct.pack(">HHH B", 1, 0, 0, 1)  # length 0 is illegal
        with self.assertRaises(ValueError):
            framer.feed(bad)


class TestTCPServer(unittest.TestCase):
    def setUp(self):
        self.map, self.dev1, self.dev2 = make_map()
        self.server = TCPServer(self.map, port=0)
        self.thread = self.server.serve_forever_in_thread()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def _connect(self):
        return socket.create_connection(("127.0.0.1", self.server.port), timeout=2)

    def test_transaction_id_echoed(self):
        with self._connect() as s:
            s.sendall(encode_mbap(0xBEEF, 1, pdu.encode_read_request(0x03, 0, 2)))
            tid, uid, payload = read_one_adu(s)
            self.assertEqual(tid, 0xBEEF)
            self.assertEqual(uid, 1)
            self.assertEqual(pdu.decode_read_response(payload), (0x03, [100, 200]))

    def test_read_input_and_holding(self):
        self.dev1._input[3] = 77
        with self._connect() as s:
            s.sendall(encode_mbap(1, 1, pdu.encode_read_request(0x04, 3, 1)))
            _tid, _uid, payload = read_one_adu(s)
            self.assertEqual(pdu.decode_read_response(payload), (0x04, [77]))

            s.sendall(encode_mbap(2, 1, pdu.encode_read_request(0x03, 0, 2)))
            _tid, _uid, payload = read_one_adu(s)
            self.assertEqual(pdu.decode_read_response(payload), (0x03, [100, 200]))

    def test_write_single_and_multiple(self):
        with self._connect() as s:
            s.sendall(encode_mbap(1, 1, pdu.encode_write_single_request(5, 0xCAFE)))
            _tid, _uid, payload = read_one_adu(s)
            self.assertEqual(payload, pdu.encode_write_single_request(5, 0xCAFE))
            self.assertEqual(self.dev1.read_holding(5, 1), [0xCAFE])

            values = list(range(20, 25))
            s.sendall(encode_mbap(2, 1, pdu.encode_write_multiple_request(20, values)))
            _tid, _uid, payload = read_one_adu(s)
            self.assertEqual(pdu.decode_write_multiple_response(payload), (20, 5))
            self.assertEqual(self.dev1.read_holding(20, 5), values)

    def test_multiple_units_isolated(self):
        with self._connect() as s:
            s.sendall(encode_mbap(1, 1, pdu.encode_read_request(0x03, 0, 2)))
            _tid, uid, p1 = read_one_adu(s)
            s.sendall(encode_mbap(2, 2, pdu.encode_read_request(0x03, 0, 2)))
            _tid, uid, p2 = read_one_adu(s)
            self.assertEqual(pdu.decode_read_response(p1), (0x03, [100, 200]))
            self.assertEqual(pdu.decode_read_response(p2), (0x03, [300, 400]))

    def test_32bit_word_order_both_units(self):
        # unit 1 = ABCD, unit 2 = DCBA: same value, different physical words
        with self._connect() as s:
            s.sendall(encode_mbap(1, 1, pdu.encode_read_request(0x03, 10, 2)))
            _tid, _uid, payload = read_one_adu(s)
            self.assertEqual(pdu.decode_read_response(payload)[1], [0x1234, 0x5678])
            s.sendall(encode_mbap(2, 2, pdu.encode_read_request(0x03, 10, 2)))
            _tid, _uid, payload = read_one_adu(s)
            self.assertEqual(pdu.decode_read_response(payload)[1], [0x5678, 0x1234])

    def test_unknown_unit_0b_exception(self):
        with self._connect() as s:
            s.sendall(encode_mbap(1, 9, pdu.encode_read_request(0x03, 0, 1)))
            _tid, _uid, payload = read_one_adu(s)
            self.assertEqual(payload[0], 0x83)
            self.assertEqual(payload[1], pdu.EXN_GATEWAY_TARGET_FAILED)

    def test_illegal_function_exception(self):
        with self._connect() as s:
            s.sendall(encode_mbap(1, 1, b"\x05\x00\x00\x00\x01"))
            _tid, _uid, payload = read_one_adu(s)
            self.assertEqual(payload, pdu.exception_response(0x05, pdu.EXN_ILLEGAL_FUNCTION))

    def test_address_boundary(self):
        # size=100: [99] is legal, [99..100] is not
        with self._connect() as s:
            s.sendall(encode_mbap(1, 1, pdu.encode_read_request(0x03, 99, 1)))
            _tid, _uid, ok = read_one_adu(s)
            self.assertEqual(pdu.decode_read_response(ok), (0x03, [0]))

            s.sendall(encode_mbap(2, 1, pdu.encode_read_request(0x03, 99, 2)))
            _tid, _uid, bad = read_one_adu(s)
            self.assertEqual(bad, pdu.exception_response(0x03, pdu.EXN_ILLEGAL_DATA_ADDRESS))

    def test_illegal_data_value(self):
        with self._connect() as s:
            # quantity 0
            s.sendall(
                encode_mbap(1, 1, bytes([0x03, 0x00, 0x00, 0x00, 0x00]))
            )
            _tid, _uid, payload = read_one_adu(s)
            self.assertEqual(
                payload, pdu.exception_response(0x03, pdu.EXN_ILLEGAL_DATA_VALUE)
            )

    def test_half_and_coalesced_frames_over_real_socket(self):
        with self._connect() as s:
            a1 = encode_mbap(1, 1, pdu.encode_read_request(0x03, 0, 1))
            a2 = encode_mbap(2, 2, pdu.encode_read_request(0x03, 0, 1))
            a3 = encode_mbap(3, 1, pdu.encode_read_request(0x03, 1, 1))
            # first half of a1 alone, then remainder glued to a2, byte at a
            # time for a3
            s.sendall(a1[:5])
            s.sendall(a1[5:] + a2)
            replies = []
            for _ in range(2):
                replies.append(read_one_adu(s))
            self.assertEqual(replies[0][0], 1)
            self.assertEqual(pdu.decode_read_response(replies[0][2])[1], [100])
            self.assertEqual(replies[1][0], 2)
            self.assertEqual(pdu.decode_read_response(replies[1][2])[1], [300])

            for b in a3:
                s.sendall(bytes([b]))
            tid, _uid, payload = read_one_adu(s)
            self.assertEqual(tid, 3)
            self.assertEqual(pdu.decode_read_response(payload)[1], [200])

    def test_nonzero_protocol_id(self):
        with self._connect() as s:
            adu = struct.pack(">HHH B", 1, 99, 6, 1) + b"\x03\x00\x00\x00\x01"
            s.sendall(adu)
            _tid, _uid, payload = read_one_adu(s)
            self.assertEqual(payload[0], 0x83)
            self.assertEqual(payload[1], pdu.EXN_SLAVE_DEVICE_FAILURE)

    def test_concurrent_connections(self):
        import threading

        errors = []

        def worker(unit, expected):
            try:
                with self._connect() as s:
                    s.sendall(
                        encode_mbap(1, unit, pdu.encode_read_request(0x03, 0, 1))
                    )
                    _tid, _uid, payload = read_one_adu(s)
                    assert pdu.decode_read_response(payload)[1] == [expected]
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(1, 100)),
            threading.Thread(target=worker, args=(2, 300)),
            threading.Thread(target=worker, args=(1, 100)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        self.assertFalse(errors)


if __name__ == "__main__":
    unittest.main()
