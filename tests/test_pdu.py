"""PDU parsing, execution and exception responses."""

import pytest

from modbus_sim.exceptions import (
    EX_ILLEGAL_DATA_ADDRESS,
    EX_ILLEGAL_DATA_VALUE,
    EX_ILLEGAL_FUNCTION,
)
from modbus_sim.pdu import (
    FC_READ_HOLDING,
    FC_READ_INPUT,
    FC_WRITE_MULTIPLE,
    FC_WRITE_SINGLE,
    encode_exception,
    execute,
    handle_pdu,
    parse_request,
)
from modbus_sim.registers import Device, DeviceConfig


def make_device():
    return Device(
        DeviceConfig(
            1,
            holding_size=10,
            input_size=5,
            holding={0: 0x1234, 1: 0x5678},
            inputs={0: 0xABCD},
        )
    )


def test_read_holding_response_layout():
    pdu = execute(make_device(), parse_request(bytes.fromhex("03 00 00 00 02")))
    assert pdu == bytes.fromhex("03 04 12 34 56 78")


def test_read_input_response_layout():
    pdu = execute(make_device(), parse_request(bytes.fromhex("04 00 00 00 01")))
    assert pdu == bytes.fromhex("04 02 AB CD")


def test_write_single_echoes_request():
    device = make_device()
    req = bytes.fromhex("06 00 02 00 7B")  # write 123 to address 2
    pdu = execute(device, parse_request(req))
    assert pdu == req
    assert device.read_holding(2, 1) == [123]


def test_write_multiple_response_and_state():
    device = make_device()
    req = bytes.fromhex("10 00 03 00 02 04 00 0A 00 14")  # [10, 20] @ addr 3
    pdu = execute(device, parse_request(req))
    assert pdu == bytes.fromhex("10 00 03 00 02")
    assert device.read_holding(3, 2) == [10, 20]


def test_exception_frame_sets_bit_7():
    assert encode_exception(0x03, EX_ILLEGAL_FUNCTION) == bytes.fromhex("83 01")


def test_illegal_function_code():
    pdu = handle_pdu(make_device(), bytes.fromhex("05 00 00 00 01"))  # 0x05 unsupported
    assert pdu == bytes.fromhex("85 01")


def test_address_out_of_range_holding():
    # device holding size is 10; start at 9 asking for 2 exceeds it
    pdu = handle_pdu(make_device(), bytes.fromhex("03 00 09 00 02"))
    assert pdu == bytes.fromhex("83 02")
    assert pdu[1] == EX_ILLEGAL_DATA_ADDRESS


def test_address_fully_past_space():
    pdu = handle_pdu(make_device(), bytes.fromhex("03 00 10 00 01"))
    assert pdu == bytes.fromhex("83 02")


def test_input_address_out_of_range():
    pdu = handle_pdu(make_device(), bytes.fromhex("04 00 04 00 02"))
    assert pdu == bytes.fromhex("84 02")


def test_write_address_out_of_range():
    pdu = handle_pdu(make_device(), bytes.fromhex("06 00 0A 00 01"))
    assert pdu == bytes.fromhex("86 02")


def test_read_quantity_zero_and_too_large():
    assert handle_pdu(make_device(), bytes.fromhex("03 00 00 00 00")) == bytes.fromhex("83 03")
    assert handle_pdu(make_device(), bytes.fromhex("03 00 00 00 7E")) == bytes.fromhex("83 03")
    # 125 is the max legal read quantity
    device = Device(DeviceConfig(1, holding_size=125, input_size=1))
    assert handle_pdu(device, bytes.fromhex("03 00 00 00 7D"))[0] == FC_READ_HOLDING


def test_write_multiple_bad_byte_count():
    # claims quantity 2 (4 bytes) but byte-count field says 3
    bad = bytes.fromhex("10 00 00 00 02 03 00 0A 00 14")
    pdu = handle_pdu(make_device(), bad)
    assert pdu == bytes.fromhex("90 03")
    assert pdu[1] == EX_ILLEGAL_DATA_VALUE


def test_truncated_pdu_length_errors():
    for raw in (b"", b"\x03\x00\x00", b"\x06\x00", b"\x10\x00\x00\x00\x02"):
        pdu = handle_pdu(make_device(), raw)
        assert pdu[1] in (EX_ILLEGAL_DATA_VALUE, EX_ILLEGAL_FUNCTION)


def test_write_quantity_limits():
    # quantity 124 exceeds the 123 register write-multiple limit
    body = b"\x10\x00\x00\x00\x7C\xF8" + b"\x00\x00" * 124
    pdu = handle_pdu(make_device(), body)
    assert pdu == bytes.fromhex("90 03")


def test_read_only_input_space_rejects_writes():
    device = make_device()
    with pytest.raises(Exception):
        device.input.write(0, [1])
