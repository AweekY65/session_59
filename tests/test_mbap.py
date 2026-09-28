"""MBAP encoding and the incremental TcpFramer (half/sticky packets)."""

import pytest

from modbus_sim.mbap import (
    MBAP_SIZE,
    FrameFormatError,
    TcpFramer,
    encode_mbap,
)


def test_encode_mbap_layout():
    frame = encode_mbap(0x1234, 0x11, b"\x03\x00")
    assert frame == bytes.fromhex("1234 0000 0003 11 03 00".replace(" ", ""))
    # length = unit id + PDU
    assert int.from_bytes(frame[4:6], "big") == 3


def test_framer_single_frame():
    framer = TcpFramer()
    frames = framer.feed(encode_mbap(1, 7, b"\x03\x02\x12\x34"))
    assert len(frames) == 1
    assert frames[0].transaction_id == 1
    assert frames[0].unit_id == 7
    assert frames[0].pdu == bytes.fromhex("03 02 12 34")


def test_framer_half_packet_byte_by_byte():
    full = encode_mbap(42, 3, b"\x03\x04\x00\x01\x00\x02")
    framer = TcpFramer()
    produced = []
    for i, byte in enumerate(full):
        produced += framer.feed(bytes([byte]))
        if i < len(full) - 1:
            assert produced == []  # nothing emitted until frame completes
    assert len(produced) == 1
    assert produced[0].transaction_id == 42
    assert produced[0].pdu == bytes.fromhex("03 04 00 01 00 02")


def test_framer_header_then_body():
    full = encode_mbap(9, 1, b"\x03\x02\xAB\xCD")
    framer = TcpFramer()
    assert framer.feed(full[:MBAP_SIZE]) == []
    (frame,) = framer.feed(full[MBAP_SIZE:])
    assert frame.unit_id == 1


def test_framer_sticky_two_frames_in_one_read():
    f1 = encode_mbap(1, 1, b"\x03\x02\x00\x0A")
    f2 = encode_mbap(2, 2, b"\x04\x02\x00\x14")
    frames = TcpFramer().feed(f1 + f2)
    assert [f.transaction_id for f in frames] == [1, 2]
    assert [f.unit_id for f in frames] == [1, 2]


def test_framer_sticky_split_across_boundary():
    f1 = encode_mbap(1, 1, b"\x03\x02\x00\x0A")
    f2 = encode_mbap(2, 2, b"\x04\x02\x00\x14")
    blob = f1 + f2
    cut = len(f1) + 2  # inside frame 2's MBAP
    framer = TcpFramer()
    first = framer.feed(blob[:cut])
    second = framer.feed(blob[cut:])
    assert [f.transaction_id for f in first + second] == [1, 2]


def test_framer_many_small_chunks_reassemble_all():
    frames_in = [encode_mbap(i, 1, bytes((0x03, 0x00, i & 0xFF))) for i in range(20)]
    blob = b"".join(frames_in)
    framer = TcpFramer()
    out = []
    for byte in blob:
        out += framer.feed(bytes([byte]))
    assert len(out) == 20


def test_framer_bad_protocol_id_rejected():
    bad = encode_mbap(1, 1, b"\x03")
    bad = bad[:2] + b"\x00\x01" + bad[4:]
    with pytest.raises(FrameFormatError):
        TcpFramer().feed(bad)


def test_framer_bad_length_rejected():
    bad = encode_mbap(1, 1, b"\x03")
    bad = bad[:4] + b"\x00\x01" + bad[6:]  # length=1 (needs >= 2)
    with pytest.raises(FrameFormatError):
        TcpFramer().feed(bad)
