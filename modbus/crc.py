"""CRC-16 (Modbus) implementation.

Polynomial 0xA001 (reversed 0x8005), init 0xFFFF, no reflection of input
needed because the table is built with the bit reversal folded in.

Test vector: crc16(b"\\x01\\x03\\x00\\x00\\x00\\x0a") == 0xE5DB
"""

from __future__ import annotations

_TABLE = []


def _build_table() -> list[int]:
    table = []
    for byte in range(256):
        crc = byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
        table.append(crc)
    return table


_TABLE = _build_table()


def crc16(data: bytes | bytearray) -> int:
    """Return the Modbus CRC-16 of *data* as a 16-bit integer."""
    crc = 0xFFFF
    for byte in data:
        crc = (crc >> 8) ^ _TABLE[(crc ^ byte) & 0xFF]
    return crc & 0xFFFF
