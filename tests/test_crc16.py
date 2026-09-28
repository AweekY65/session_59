"""CRC-16/MODBUS tests using the published check value and frame roundtrips."""

from modbus_sim.crc16 import append_crc, check_crc, crc16


def test_known_check_vector():
    # CRC-16/MODBUS check value for ASCII "123456789" is 0x4B37.
    assert crc16(b"123456789") == 0x4B37


def test_empty_uses_init_value():
    assert crc16(b"") == 0xFFFF


def test_append_and_check_roundtrip():
    payload = bytes.fromhex("01 03 00 00 00 0A")
    frame = append_crc(payload)
    assert len(frame) == len(payload) + 2
    # CRC transmitted low byte first
    crc = crc16(payload)
    assert frame[-2] == crc & 0xFF
    assert frame[-1] == (crc >> 8) & 0xFF
    assert check_crc(frame)


def test_check_crc_detects_corruption():
    frame = bytearray(append_crc(bytes.fromhex("01 03 00 00 00 0A")))
    frame[3] ^= 0xFF
    assert not check_crc(bytes(frame))


def test_check_crc_too_short():
    assert not check_crc(b"\x01")
