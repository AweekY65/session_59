"""End-to-end RTU tests over localhost (no real serial port)."""

import socket
import threading

import pytest

from modbus_sim.client import ModbusResponseError, RtuClient
from modbus_sim.config import build_bus
from modbus_sim.crc16 import crc16
from modbus_sim.registers import DeviceConfig
from modbus_sim.rtu import decode_rtu, encode_rtu
from modbus_sim.server import RtuOverTcpServer


def _make_server(**kwargs):
    bus = build_bus(
        [
            DeviceConfig(
                1,
                holding_size=10,
                input_size=5,
                holding={0: 0x1234, 1: 0x5678, 9: 0xFFFF},
                inputs={0: 0xABCD},
            ),
            DeviceConfig(2, holding_size=10, input_size=5, holding={0: 7}),
        ]
    )
    return RtuOverTcpServer(bus, frame_gap_timeout=0.1, **kwargs), bus


@pytest.fixture()
def server():
    srv, _bus = _make_server()
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture()
def client(server):
    with RtuClient(port=server.port) as c:
        yield c


def test_rtu_read_holding(client):
    assert client.read_holding(1, 0, 2) == [0x1234, 0x5678]


def test_rtu_read_input(client):
    assert client.read_input(1, 0, 1) == [0xABCD]


def test_rtu_write_single_and_multiple(client):
    assert client.write_single(1, 2, 1234) == 1234
    assert client.read_holding(1, 2, 1) == [1234]
    assert client.write_multiple(1, 3, [11, 12]) == 2
    assert client.read_holding(1, 3, 2) == [11, 12]


def test_rtu_unit_isolation(client):
    client.write_single(1, 5, 0xAAAA)
    client.write_single(2, 5, 0x5555)
    assert client.read_holding(1, 5, 1) == [0xAAAA]
    assert client.read_holding(2, 5, 1) == [0x5555]


def test_rtu_unknown_unit_silently_ignored(client):
    # A frame to unit 9 must produce NO response (RTU bus rule).
    raw = encode_rtu(9, bytes.fromhex("03 00 00 00 01"))
    client.send_raw(raw)
    assert client.recv_raw() == b""


def test_rtu_broadcast_no_response(client):
    raw = encode_rtu(0, bytes.fromhex("06 00 00 00 01"))
    client.send_raw(raw)
    assert client.recv_raw() == b""


def test_rtu_illegal_function_exception(client):
    pdu = client._roundtrip(1, bytes.fromhex("05 00 00 00 01"))
    assert pdu == bytes.fromhex("85 01")


def test_rtu_illegal_address_exception(client):
    with pytest.raises(ModbusResponseError) as exc:
        client.read_holding(1, 10, 1)
    assert exc.value.exception_code == 0x02


def test_rtu_bad_crc_silently_dropped_by_default(server):
    with RtuClient(port=server.port) as c:
        bad = bytearray(encode_rtu(1, bytes.fromhex("03 00 00 00 01")))
        bad[3] ^= 0xFF  # corrupt payload byte; CRC now wrong
        good = encode_rtu(1, bytes.fromhex("03 00 09 00 01"))
        c.send_raw(bytes(bad) + good)
        # only the good frame (holding[9] = 0xFFFF) is answered
        blob = b""
        c.sock.settimeout(2)
        while not blob:
            blob = c.sock.recv(256)
        frames = c._framer.feed(blob)
        assert len(frames) == 1
        parsed = decode_rtu(frames[0])
        assert parsed.unit_id == 1
        assert parsed.pdu == bytes.fromhex("03 02 FF FF")


def test_rtu_bad_crc_with_explicit_error_response():
    srv, _bus = _make_server(crc_error_response=True)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        sock = socket.create_connection(("127.0.0.1", srv.port), timeout=5)
        bad = bytearray(encode_rtu(1, bytes.fromhex("03 00 00 00 01")))
        bad[3] ^= 0xFF
        sock.sendall(bytes(bad))
        sock.settimeout(2)
        reply = sock.recv(64)
        parsed = decode_rtu(reply)
        assert parsed.unit_id == 1
        assert parsed.pdu == bytes.fromhex("80 04")
        sock.close()
    finally:
        srv.shutdown()
        srv.server_close()


def test_rtu_sticky_two_frames_one_send(client):
    f1 = encode_rtu(1, bytes.fromhex("03 00 00 00 01"))
    f2 = encode_rtu(2, bytes.fromhex("03 00 00 00 01"))
    client.send_raw(f1 + f2)
    blob = b""
    client.sock.settimeout(2)
    while len(blob) < 14:
        chunk = client.sock.recv(256)
        if not chunk:
            break
        blob += chunk
    frames = client._framer.feed(blob)
    assert len(frames) == 2
    assert decode_rtu(frames[0]).unit_id == 1
    assert decode_rtu(frames[1]).unit_id == 2


def test_rtu_half_frame_byte_by_byte(client):
    raw = encode_rtu(1, bytes.fromhex("03 00 04 00 01"))
    client.write_single(1, 4, 0x002A)
    # now send a read request one byte at a time and verify reassembly
    for byte in raw:
        client.send_raw(bytes([byte]))
    client.sock.settimeout(2)
    blob = b""
    while not blob:
        blob = client.sock.recv(256)
    frame = decode_rtu(client._framer.feed(blob)[0])
    assert frame.pdu == bytes.fromhex("03 02 00 2A")


def test_rtu_crc_value_on_wire():
    # Sanity: a real frame verifies and a flipped crc byte doesn't.
    frame = encode_rtu(1, bytes.fromhex("03 00 00 00 01"))
    crc = crc16(frame[:-2])
    assert frame[-2:] == bytes((crc & 0xFF, crc >> 8))
    broken = bytearray(frame)
    broken[-1] ^= 0x01
    from modbus_sim.crc16 import check_crc

    assert not check_crc(bytes(broken))
