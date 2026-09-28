"""End-to-end tests over localhost TCP sockets (no real serial/PLC)."""

import socket
import threading

import pytest

from modbus_sim.client import ModbusResponseError, TcpClient
from modbus_sim.config import build_bus
from modbus_sim.mbap import encode_mbap
from modbus_sim.registers import DeviceConfig, WordOrder
from modbus_sim.server import TcpModbusServer


@pytest.fixture(scope="module")
def server():
    bus = build_bus(
        [
            DeviceConfig(
                1,
                holding_size=10,
                input_size=5,
                word_order=WordOrder.ABCD,
                holding={0: 0x1234, 1: 0x5678, 9: 0xFFFF},
                inputs={0: 0xABCD, 4: 0x0001},
            ),
            DeviceConfig(
                2,
                holding_size=10,
                input_size=5,
                word_order=WordOrder.CDAB,
                holding={0: 0x3344, 1: 0x1122},
            ),
        ]
    )
    srv = TcpModbusServer(bus, port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture()
def client(server):
    with TcpClient(port=server.port) as c:
        yield c


def test_read_holding(client):
    assert client.read_holding(1, 0, 2) == [0x1234, 0x5678]


def test_read_input(client):
    assert client.read_input(1, 0, 1) == [0xABCD]


def test_write_single_then_read(client):
    assert client.write_single(1, 2, 4242) == 4242
    assert client.read_holding(1, 2, 1) == [4242]


def test_write_multiple_then_read(client):
    assert client.write_multiple(2, 4, [7, 8, 9]) == 3
    assert client.read_holding(2, 4, 3) == [7, 8, 9]


def test_units_are_isolated(client):
    client.write_single(1, 3, 0xAAAA)
    client.write_single(2, 3, 0x5555)
    assert client.read_holding(1, 3, 1) == [0xAAAA]
    assert client.read_holding(2, 3, 1) == [0x5555]


def test_unknown_unit_id_cannot_reach_others(client):
    # unit 99 does not exist -> gateway-target-no-response 0x0B
    with pytest.raises(ModbusResponseError) as exc:
        client.read_holding(99, 0, 1)
    assert exc.value.exception_code == 0x0B
    # and existing devices are untouched
    assert client.read_holding(1, 0, 1) == [0x1234]


def test_illegal_function_over_tcp(client):
    pdu = client._roundtrip(1, bytes.fromhex("05 00 00 00 01"))
    assert pdu == bytes.fromhex("85 01")


def test_address_boundary_over_tcp(client):
    # last register readable
    assert client.read_holding(1, 9, 1) == [0xFFFF]
    assert client.read_input(1, 4, 1) == [0x0001]
    # one beyond is illegal
    with pytest.raises(ModbusResponseError) as exc:
        client.read_holding(1, 10, 1)
    assert exc.value.exception_code == 0x02
    with pytest.raises(ModbusResponseError) as exc:
        client.write_single(1, 10, 1)
    assert exc.value.exception_code == 0x02


def test_32bit_word_order_across_devices(client):
    # unit 1 ABCD: write 0x11223344 as two registers directly
    client.write_multiple(1, 5, [0x1122, 0x3344])
    assert client.read_holding(1, 5, 2) == [0x1122, 0x3344]
    # unit 2 is CDAB word-swapped
    client.write_multiple(2, 5, [0x3344, 0x1122])
    assert client.read_holding(2, 5, 2) == [0x3344, 0x1122]


def test_raw_socket_half_and_sticky_frames(server):
    """Feed one frame byte-at-a-time and two frames back-to-back."""
    sock = socket.create_connection(("127.0.0.1", server.port), timeout=5)
    try:
        request = encode_mbap(100, 1, bytes.fromhex("03 00 00 00 02"))
        for byte in request:  # half packet: one byte per send
            sock.sendall(bytes([byte]))
        head = _recv_exact(sock, 7)
        length = int.from_bytes(head[4:6], "big")
        body = _recv_exact(sock, length - 1)
        assert head[0:2] == (100).to_bytes(2, "big")
        assert body == bytes.fromhex("03 04 12 34 56 78")

        # sticky: two ADUs in one send (write-single then read)
        r1 = encode_mbap(200, 2, bytes.fromhex("06 00 00 00 07"))
        r2 = encode_mbap(201, 2, bytes.fromhex("03 00 00 00 02"))
        sock.sendall(r1 + r2)
        blob = _recv_exact(sock, 12 + 13)
        assert blob[0:2] == (200).to_bytes(2, "big")
        assert blob[12:14] == (201).to_bytes(2, "big")
        # read response after the write: holding regs [0]=7, [1]=0x1122
        assert blob[-4:] == bytes.fromhex("00 07 11 22")
    finally:
        sock.close()


def test_crossed_fragmentation(server):
    """Split the stream at arbitrary offsets; frames must still reassemble."""
    sock = socket.create_connection(("127.0.0.1", server.port), timeout=5)
    try:
        requests = b"".join(
            encode_mbap(i, 1, bytes.fromhex("03 00 00 00 01")) for i in range(1, 8)
        )
        # 3-byte chunks guarantees cuts through MBAP and PDU fields
        for i in range(0, len(requests), 3):
            sock.sendall(requests[i : i + 3])
        for tid in range(1, 8):
            head = _recv_exact(sock, 7)
            body = _recv_exact(sock, int.from_bytes(head[4:6], "big") - 1)
            assert int.from_bytes(head[0:2], "big") == tid
            assert body == bytes.fromhex("03 02 12 34")
    finally:
        sock.close()


def _recv_exact(sock, n):
    chunks = []
    remaining = n
    while remaining:
        chunk = sock.recv(remaining)
        assert chunk, "connection closed early"
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)
