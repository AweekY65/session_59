"""Minimal blocking clients used by the CLI and tests (localhost only)."""

from __future__ import annotations

import socket

from .exceptions import ModbusError
from .mbap import TcpFramer, encode_mbap
from .pdu import (
    FC_READ_HOLDING,
    FC_READ_INPUT,
    FC_WRITE_SINGLE,
    FC_WRITE_MULTIPLE,
)
from .rtu import RtuFramer, decode_rtu, encode_rtu


class ModbusResponseError(ModbusError):
    def __init__(self, function: int, exception_code: int):
        super().__init__(
            f"modbus exception: function 0x{function:02X} code 0x{exception_code:02X}"
        )
        self.function = function
        self.exception_code = exception_code


def _read_pdu(pdu: bytes):
    """Decode a successful response PDU (raise on exception responses)."""
    fc = pdu[0]
    if fc & 0x80:
        raise ModbusResponseError(fc & 0x7F, pdu[1])
    if fc in (FC_READ_HOLDING, FC_READ_INPUT):
        count = pdu[1]
        return [
            int.from_bytes(pdu[2 + 2 * i : 4 + 2 * i], "big")
            for i in range(count // 2)
        ]
    if fc == FC_WRITE_SINGLE:
        return int.from_bytes(pdu[3:5], "big")
    if fc == FC_WRITE_MULTIPLE:
        return int.from_bytes(pdu[3:5], "big")
    raise ModbusError(f"unexpected response function 0x{fc:02X}")


class TcpClient:
    """One connection to a :class:`TcpModbusServer`."""

    def __init__(self, host: str = "127.0.0.1", port: int = 1502, timeout: float = 5):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self._tid = 0
        self._framer = TcpFramer()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.sock.close()

    def _next_tid(self) -> int:
        self._tid = (self._tid + 1) & 0xFFFF
        return self._tid

    def _roundtrip(self, unit_id: int, pdu: bytes) -> bytes:
        tid = self._next_tid()
        self.sock.sendall(encode_mbap(tid, unit_id, pdu))
        while True:
            frames = self._framer.feed(self.sock.recv(4096))
            for frame in frames:
                if frame.transaction_id == tid:
                    return frame.pdu
                # late reply to an older request; keep draining

    def read_holding(self, unit_id: int, address: int, count: int) -> list[int]:
        pdu = bytes((FC_READ_HOLDING,)) + address.to_bytes(2, "big") + count.to_bytes(2, "big")
        return _read_pdu(self._roundtrip(unit_id, pdu))

    def read_input(self, unit_id: int, address: int, count: int) -> list[int]:
        pdu = bytes((FC_READ_INPUT,)) + address.to_bytes(2, "big") + count.to_bytes(2, "big")
        return _read_pdu(self._roundtrip(unit_id, pdu))

    def write_single(self, unit_id: int, address: int, value: int) -> int:
        pdu = (
            bytes((FC_WRITE_SINGLE,))
            + address.to_bytes(2, "big")
            + (value & 0xFFFF).to_bytes(2, "big")
        )
        return _read_pdu(self._roundtrip(unit_id, pdu))

    def write_multiple(self, unit_id: int, address: int, values) -> int:
        values = list(values)
        pdu = (
            bytes((FC_WRITE_MULTIPLE,))
            + address.to_bytes(2, "big")
            + len(values).to_bytes(2, "big")
            + bytes((len(values) * 2,))
            + b"".join((v & 0xFFFF).to_bytes(2, "big") for v in values)
        )
        return _read_pdu(self._roundtrip(unit_id, pdu))


class RtuClient:
    """RTU client talking to an :class:`RtuOverTcpServer` (localhost)."""

    def __init__(self, host: str = "127.0.0.1", port: int = 1503, timeout: float = 5):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self._framer = RtuFramer(expect_response=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.sock.close()

    def _roundtrip(self, unit_id: int, pdu: bytes) -> bytes:
        self.sock.sendall(encode_rtu(unit_id, pdu))
        while True:
            data = self.sock.recv(4096)
            if not data:
                raise ConnectionError("rtu connection closed")
            for raw in self._framer.feed(data):
                frame = decode_rtu(raw)  # raises RtuDecodeError on bad CRC
                if frame.unit_id == unit_id:
                    return frame.pdu

    def read_holding(self, unit_id: int, address: int, count: int) -> list[int]:
        pdu = bytes((FC_READ_HOLDING,)) + address.to_bytes(2, "big") + count.to_bytes(2, "big")
        return _read_pdu(self._roundtrip(unit_id, pdu))

    def read_input(self, unit_id: int, address: int, count: int) -> list[int]:
        pdu = bytes((FC_READ_INPUT,)) + address.to_bytes(2, "big") + count.to_bytes(2, "big")
        return _read_pdu(self._roundtrip(unit_id, pdu))

    def write_single(self, unit_id: int, address: int, value: int) -> int:
        pdu = (
            bytes((FC_WRITE_SINGLE,))
            + address.to_bytes(2, "big")
            + (value & 0xFFFF).to_bytes(2, "big")
        )
        return _read_pdu(self._roundtrip(unit_id, pdu))

    def write_multiple(self, unit_id: int, address: int, values) -> int:
        values = list(values)
        pdu = (
            bytes((FC_WRITE_MULTIPLE,))
            + address.to_bytes(2, "big")
            + len(values).to_bytes(2, "big")
            + bytes((len(values) * 2,))
            + b"".join((v & 0xFFFF).to_bytes(2, "big") for v in values)
        )
        return _read_pdu(self._roundtrip(unit_id, pdu))

    def send_raw(self, data: bytes) -> None:
        """Inject arbitrary bytes (bad CRC / half frames) for tests."""
        self.sock.sendall(data)

    def recv_raw(self, size: int = 256) -> bytes:
        self.sock.settimeout(1.0)
        try:
            return self.sock.recv(size)
        except (TimeoutError, socket.timeout):
            return b""
