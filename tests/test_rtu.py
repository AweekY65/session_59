"""RTU framing: encode/decode, CRC validation, sticky/half frame reassembly."""

import pytest

from modbus_sim.crc16 import append_crc
from modbus_sim.rtu import (
    RtuDecodeError,
    RtuFramer,
    decode_rtu,
    encode_rtu,
)


def test_encode_rtu_matches_hand_crc():
    pdu = bytes.fromhex("03 00 00 00 0A")
    frame = encode_rtu(0x01, pdu)
    assert frame == append_crc(bytes.fromhex("01 03 00 00 00 0A"))


def test_decode_roundtrip():
    frame = encode_rtu(0x11, bytes.fromhex("03 00 00 00 02"))
    parsed = decode_rtu(frame)
    assert parsed.unit_id == 0x11
    assert parsed.pdu == bytes.fromhex("03 00 00 00 02")


def test_bad_crc_raises():
    frame = bytearray(encode_rtu(1, bytes.fromhex("03 00 00 00 02")))
    frame[4] ^= 0x01
    with pytest.raises(RtuDecodeError):
        decode_rtu(bytes(frame))


def test_frame_too_short():
    with pytest.raises(RtuDecodeError):
        decode_rtu(b"\x01\x03")


def test_framer_fixed_request_sticky_two_frames():
    f1 = encode_rtu(1, bytes.fromhex("03 00 00 00 02"))
    f2 = encode_rtu(2, bytes.fromhex("06 00 01 00 7B"))
    frames = RtuFramer().feed(f1 + f2)
    assert len(frames) == 2
    assert decode_rtu(frames[0]).unit_id == 1
    assert decode_rtu(frames[1]).unit_id == 2


def test_framer_half_packet():
    full = encode_rtu(1, bytes.fromhex("03 00 00 00 02"))
    framer = RtuFramer()
    assert framer.feed(full[:3]) == []
    assert framer.feed(full[3:]) == [full]


def test_framer_write_multiple_length_from_byte_count():
    pdu = bytes.fromhex("10 00 00 00 03 06 00 01 00 02 00 03")
    full = encode_rtu(5, pdu)
    frames = RtuFramer().feed(full)
    assert len(frames) == 1
    assert decode_rtu(frames[0]).unit_id == 5


def test_framer_response_side_uses_byte_count():
    # server response: addr fc bytecount data... crc
    response = append_crc(bytes.fromhex("01 03 04 00 0A 00 14"))
    framer = RtuFramer(expect_response=True)
    frames = framer.feed(response + response)
    assert len(frames) == 2


def test_framer_exception_response_is_five_bytes():
    exc = append_crc(bytes.fromhex("01 83 02"))
    frames = RtuFramer(expect_response=True).feed(exc)
    assert len(frames) == 1
    assert decode_rtu(frames[0]).pdu == bytes.fromhex("83 02")


def test_framer_end_stream_flushes_residual():
    framer = RtuFramer()
    framer.feed(b"\x01")
    assert framer.end_stream() == b"\x01"
    assert framer.end_stream() == b""
