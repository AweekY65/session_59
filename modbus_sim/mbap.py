"""Modbus TCP MBAP header and a stateful stream framer.

MBAP (7 bytes) + PDU::

    transaction id (2, big-endian)
    protocol id    (2, must be 0)
    length         (2, big-endian: number of following bytes = unit id + PDU)
    unit id        (1)

The framer is fed arbitrary chunks from a socket ``recv`` (half frames and
back-to-back frames are both normal) and yields complete frames.  It never
assumes one read equals one frame.
"""

from __future__ import annotations

from dataclasses import dataclass

MBAP_SIZE = 7
MIN_LENGTH = 2  # unit id + at least a function code
MAX_MBAP_LENGTH = 254  # unit id + max PDU 253


@dataclass
class MBAP:
    transaction_id: int
    protocol_id: int
    unit_id: int
    length: int  # value carried in the length field (unit id + PDU bytes)


def encode_mbap(transaction_id: int, unit_id: int, pdu: bytes) -> bytes:
    """Build a complete TCP ADU for *pdu*."""
    length = len(pdu) + 1
    return (
        transaction_id.to_bytes(2, "big")
        + b"\x00\x00"
        + length.to_bytes(2, "big")
        + bytes((unit_id,))
        + pdu
    )


class FrameFormatError(ValueError):
    """Raised when the byte stream cannot be a Modbus TCP stream."""


@dataclass
class TcpFrame:
    mbap: MBAP
    pdu: bytes

    @property
    def unit_id(self) -> int:
        return self.mbap.unit_id

    @property
    def transaction_id(self) -> int:
        return self.mbap.transaction_id


class TcpFramer:
    """Incremental TCP ADU reassembler.

    Feed :meth:`feed` exactly what ``socket.recv`` returned.  It returns a
    list of zero or more fully reassembled :class:`TcpFrame` objects and
    buffers the remainder internally until more bytes arrive.
    """

    def __init__(self):
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[TcpFrame]:
        self._buf.extend(data)
        frames: list[TcpFrame] = []
        while True:
            frame = self._try_extract_one()
            if frame is None:
                break
            frames.append(frame)
        return frames

    def _try_extract_one(self) -> TcpFrame | None:
        buf = self._buf
        if len(buf) < MBAP_SIZE:
            return None  # header still arriving

        protocol_id = int.from_bytes(buf[2:4], "big")
        if protocol_id != 0:
            raise FrameFormatError(f"protocol id must be 0, got {protocol_id}")

        length = int.from_bytes(buf[4:6], "big")
        if not MIN_LENGTH <= length <= MAX_MBAP_LENGTH:
            raise FrameFormatError(f"mbap length {length} out of range 2..254")

        total = MBAP_SIZE + (length - 1)  # length counts unit id, not header
        if len(buf) < total:
            return None  # PDU still arriving (half packet)

        transaction_id = int.from_bytes(buf[0:2], "big")
        unit_id = buf[6]
        pdu = bytes(buf[7:total])
        del buf[:total]  # consume exactly one ADU; rest may contain the next
        return TcpFrame(MBAP(transaction_id, 0, unit_id, length), pdu)
