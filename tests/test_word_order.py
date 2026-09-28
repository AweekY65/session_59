"""32-bit conversions under all four configurable word/byte orders."""

import struct

import pytest

from modbus_sim.registers import Device, DeviceConfig, WordOrder, _u32_to_words, _words_to_u32

# For value 0x11223344:
#   ABCD -> registers [1122, 3344]
#   CDAB -> registers [3344, 1122]
#   BADC -> registers [2211, 4433]
#   DCBA -> registers [4433, 2211]
EXPECTED_WORDS = {
    WordOrder.ABCD: [0x1122, 0x3344],
    WordOrder.CDAB: [0x3344, 0x1122],
    WordOrder.BADC: [0x2211, 0x4433],
    WordOrder.DCBA: [0x4433, 0x2211],
}


@pytest.mark.parametrize("order", list(WordOrder))
def test_all_orders_encode_layout(order):
    words = _u32_to_words(0x11223344, order)
    assert words == EXPECTED_WORDS[order]


@pytest.mark.parametrize("order", list(WordOrder))
def test_all_orders_roundtrip(order):
    for value in (0, 1, 0x11223344, 0xFFFFFFFF, 0x80000000, 0xCAFEBABE):
        assert _words_to_u32(_u32_to_words(value, order), order) == value


def test_u32_device_api():
    device = Device(DeviceConfig(1, holding_size=10, word_order=WordOrder.CDAB))
    device.write_u32(2, 0x11223344)
    # word-swapped: low word stored at lower address
    assert device.read_holding(2, 2) == [0x3344, 0x1122]
    assert device.read_u32(2) == 0x11223344


def test_signed_32_bit():
    device = Device(DeviceConfig(1, holding_size=10))
    device.write_u32(0, 0xFFFFFFFF)
    assert device.read_i32(0) == -1


def test_float32_roundtrip():
    device = Device(DeviceConfig(1, holding_size=10, word_order=WordOrder.DCBA))
    device.write_float32(0, 3.14)
    assert device.read_float32(0) == pytest.approx(3.14, abs=1e-6)
    # DCBA: raw registers carry the value fully little-endian in 4 bytes
    raw = struct.pack("<f", 3.14)
    assert device.read_holding(0, 2) == [
        int.from_bytes(raw[0:2], "big"),
        int.from_bytes(raw[2:4], "big"),
    ]


def test_independent_spaces():
    device = Device(DeviceConfig(1, holding_size=4, input_size=4))
    device.write_u32(0, 0xAABBCCDD)
    assert device.read_input(0, 1) == [0]  # inputs untouched
