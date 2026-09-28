"""CRC-16/MODBUS checksum (polynomial 0xA001, reflected, init 0xFFFF).

Used by Modbus RTU frames. The CRC is sent little-endian on the wire:
low byte first, then high byte.
"""

CRC_INIT = 0xFFFF
CRC_POLY = 0xA001  # 0x8005 reflected


def crc16(data: bytes, crc: int = CRC_INIT) -> int:
    """Return the CRC16 of *data* (0..0xFFFF).

    *crc* may be supplied to continue a running calculation.
    """
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ CRC_POLY
            else:
                crc >>= 1
    return crc & 0xFFFF


def append_crc(data: bytes) -> bytes:
    """Append the RTU CRC (low byte, then high byte) to *data*."""
    crc = crc16(data)
    return bytes(data) + bytes((crc & 0xFF, (crc >> 8) & 0xFF))


def check_crc(frame: bytes) -> bool:
    """Verify an RTU *frame* that ends with its two CRC bytes."""
    if len(frame) < 2:
        return False
    payload, tail = frame[:-2], frame[-2:]
    expected = crc16(payload)
    return tail == bytes((expected & 0xFF, (expected >> 8) & 0xFF))
