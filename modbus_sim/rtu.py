"""Modbus RTU framing: PDU + CRC16.

Wire layout::

    address(1) | PDU | CRC low | CRC high

Because RTU has no length field, frame boundaries on a real serial bus are
delimited by an idle gap (>3.5 char times).  This local implementation
runs over localhost TCP for testing and reassembles frames from their known
fixed/variable sizes for the supported function codes, plus an idle-gap
callback for truly unknown function codes (used by the server when a recv
timeout fires).

Known request PDU sizes (after the function byte):

* 0x03 / 0x04 read:          4 bytes  -> whole frame 8
* 0x06 write single:         4 bytes  -> whole frame 8
* 0x10 write multiple:       5 + 2*qty -> byte-count driven
"""

from __future__ import annotations

from dataclasses import dataclass

from .crc16 import append_crc, check_crc
from .pdu import (
    FC_READ_HOLDING,
    FC_READ_INPUT,
    FC_WRITE_MULTIPLE,
    FC_WRITE_SINGLE,
)

RTU_MIN_FRAME = 4  # address + function + 2 crc

# Whole-frame length for fixed-size requests/responses.
_FIXED_REQUEST = {
    FC_READ_HOLDING: 8,
    FC_READ_INPUT: 8,
    FC_WRITE_SINGLE: 8,
}
# write-single response and write-multiple response are both exactly 8.
_FIXED_RESPONSE = {FC_WRITE_SINGLE: 8, FC_WRITE_MULTIPLE: 8}


class RtuDecodeError(ValueError):
    """Raised when an RTU frame is structurally invalid or its CRC fails."""


@dataclass
class RtuFrame:
    unit_id: int
    pdu: bytes


def encode_rtu(unit_id: int, pdu: bytes) -> bytes:
    """Build an RTU frame: address + PDU + CRC16."""
    if not 0 <= unit_id <= 247:
        raise ValueError("rtu unit id must be 0..247")
    return append_crc(bytes((unit_id,)) + pdu)


def decode_rtu(frame: bytes) -> RtuFrame:
    """Validate CRC and split a complete RTU frame."""
    if len(frame) < RTU_MIN_FRAME:
        raise RtuDecodeError("frame too short")
    if not check_crc(frame):
        raise RtuDecodeError("CRC mismatch")
    return RtuFrame(frame[0], frame[1:-2])


def _rtu_frame_len(
    buf: bytes, expect_response: bool, fixed_response_fc: int | None
) -> int | None:
    """Return the expected whole-frame length, or None if unknown/short.

    Raises RtuDecodeError if a frame that is already complete is malformed.
    """
    if len(buf) < 2:
        return None
    fc = buf[1] & 0x7F  # exception responses set bit 7

    # Exception responses are always address + fc + code + CRC = 5 bytes.
    if buf[1] & 0x80:
        return 5

    if not expect_response:
        if fc in _FIXED_REQUEST:
            return _FIXED_REQUEST[fc]
        if fc == FC_WRITE_MULTIPLE:
            if len(buf) < 7:
                return None
            # addr+fc+addr(2)+qty(2)+bytecount = 7 bytes, then data, then CRC
            return 7 + buf[6] + 2
        return None  # unknown function: rely on idle-gap flush

    # Response parsing.
    if fc in (FC_READ_HOLDING, FC_READ_INPUT):
        if len(buf) < 3:
            return None
        return 3 + buf[2] + 2  # addr+fc+bytecount + data + CRC
    if fixed_response_fc is not None and fc in _FIXED_RESPONSE:
        return _FIXED_RESPONSE[fc]
    if fc in _FIXED_RESPONSE:
        return _FIXED_RESPONSE[fc]
    return None


class RtuFramer:
    """Reassemble RTU frames from arbitrary TCP chunks (localhost only)."""

    def __init__(self, expect_response: bool = False,
                 fixed_response_fc: int | None = None):
        self._buf = bytearray()
        self._expect_response = expect_response
        self._fixed_response_fc = fixed_response_fc

    @property
    def buffered(self) -> int:
        return len(self._buf)

    def feed(self, data: bytes) -> list[bytes]:
        """Return a list of complete raw frames (CRC not yet checked)."""
        self._buf.extend(data)
        frames: list[bytes] = []
        while True:
            total = _rtu_frame_len(
                self._buf, self._expect_response, self._fixed_response_fc
            )
            if total is None:
                break
            if len(self._buf) < total:
                break
            frames.append(bytes(self._buf[:total]))
            del self._buf[:total]
        return frames

    def end_stream(self) -> bytes:
        """Flush and return residual bytes (e.g. after a serial idle gap).

        Returns b"" when nothing is buffered.
        """
        tail = bytes(self._buf)
        self._buf.clear()
        return tail
