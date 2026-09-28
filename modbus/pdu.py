"""Modbus PDU (Protocol Data Unit) encode/decode.

A PDU is ``function code + function data``; it contains no unit id, no MBAP
header and no CRC -- those are added by the RTU/TCP transport layers.

All multi-byte fields on the wire are big-endian (Modbus "network byte
order").  32-bit values spanning two registers are handled in device.py and
their *word* order is configurable there.

Exception codes (MODBUS Application Protocol V1.1b3, Table 6.3):

    01 Illegal Function
    02 Illegal Data Address
    03 Illegal Data Value
    04 Slave Device Failure
    0A Gateway Path Unavailable
    0B Gateway Target Device Failed To Respond
"""

from __future__ import annotations

from collections.abc import Sequence

# Function codes -------------------------------------------------------------
FC_READ_HOLDING_REGISTERS = 0x03
FC_READ_INPUT_REGISTERS = 0x04
FC_WRITE_SINGLE_REGISTER = 0x06
FC_WRITE_MULTIPLE_REGISTERS = 0x10

_READ_FCS = (FC_READ_HOLDING_REGISTERS, FC_READ_INPUT_REGISTERS)

# Limits from the Modbus application protocol specification.
MAX_READ_QUANTITY = 125
MAX_WRITE_MULTIPLE_QUANTITY = 123

# Exception codes ------------------------------------------------------------
EXN_ILLEGAL_FUNCTION = 0x01
EXN_ILLEGAL_DATA_ADDRESS = 0x02
EXN_ILLEGAL_DATA_VALUE = 0x03
EXN_SLAVE_DEVICE_FAILURE = 0x04
EXN_GATEWAY_PATH_UNAVAILABLE = 0x0A
EXN_GATEWAY_TARGET_FAILED = 0x0B


class ModbusError(Exception):
    """Base class for errors that map to a Modbus exception code."""

    code = EXN_SLAVE_DEVICE_FAILURE


class IllegalFunction(ModbusError):
    code = EXN_ILLEGAL_FUNCTION


class IllegalDataAddress(ModbusError):
    code = EXN_ILLEGAL_DATA_ADDRESS


class IllegalDataValue(ModbusError):
    code = EXN_ILLEGAL_DATA_VALUE


class SlaveDeviceFailure(ModbusError):
    code = EXN_SLAVE_DEVICE_FAILURE


class GatewayTargetFailed(ModbusError):
    code = EXN_GATEWAY_TARGET_FAILED


def exception_response(function_code: int, exception_code: int) -> bytes:
    """Build an exception response PDU (function code with 0x80 bit set)."""
    return bytes([function_code | 0x80, exception_code & 0xFF])


def is_exception_response(pdu: bytes | bytearray) -> bool:
    return len(pdu) >= 1 and bool(pdu[0] & 0x80)


# Read holding / input registers (0x03, 0x04) -------------------------------
def encode_read_request(function_code: int, address: int, quantity: int) -> bytes:
    if function_code not in _READ_FCS:
        raise IllegalFunction(f"not a read function code: 0x{function_code:02x}")
    if not 0 <= address <= 0xFFFF or not 1 <= quantity <= MAX_READ_QUANTITY:
        raise IllegalDataValue("address out of range or quantity not in 1..125")
    return bytes([function_code]) + address.to_bytes(2, "big") + quantity.to_bytes(2, "big")


def decode_read_request(pdu: bytes | bytearray) -> tuple[int, int, int]:
    """Return (function_code, address, quantity)."""
    if len(pdu) != 5:
        raise IllegalDataValue(f"read request must be 5 bytes, got {len(pdu)}")
    fc = pdu[0]
    if fc not in _READ_FCS:
        raise IllegalFunction(f"0x{fc:02x} is not a read function code")
    address = int.from_bytes(pdu[1:3], "big")
    quantity = int.from_bytes(pdu[3:5], "big")
    if not 1 <= quantity <= MAX_READ_QUANTITY:
        raise IllegalDataValue(f"read quantity {quantity} not in 1..125")
    return fc, address, quantity


def encode_read_response(function_code: int, values: Sequence[int]) -> bytes:
    if function_code not in _READ_FCS:
        raise IllegalFunction(f"not a read function code: 0x{function_code:02x}")
    if not 1 <= len(values) <= MAX_READ_QUANTITY:
        raise IllegalDataValue("value count not in 1..125")
    body = bytearray([function_code, len(values) * 2])
    for value in values:
        if not 0 <= value <= 0xFFFF:
            raise IllegalDataValue(f"register value 0x{value:x} does not fit in 16 bits")
        body += value.to_bytes(2, "big")
    return bytes(body)


def decode_read_response(pdu: bytes | bytearray) -> tuple[int, list[int]]:
    if len(pdu) < 2:
        raise IllegalDataValue("read response too short")
    fc = pdu[0]
    if fc not in _READ_FCS:
        raise IllegalFunction(f"0x{fc:02x} is not a read function code")
    byte_count = pdu[1]
    if byte_count == 0 or byte_count % 2 != 0 or len(pdu) != 2 + byte_count:
        raise IllegalDataValue("inconsistent byte count in read response")
    values = [int.from_bytes(pdu[i : i + 2], "big") for i in range(2, len(pdu), 2)]
    return fc, values


# Write single register (0x06) ----------------------------------------------
def encode_write_single_request(address: int, value: int) -> bytes:
    if not 0 <= address <= 0xFFFF or not 0 <= value <= 0xFFFF:
        raise IllegalDataValue("address/value out of 16-bit range")
    return bytes([FC_WRITE_SINGLE_REGISTER]) + address.to_bytes(2, "big") + value.to_bytes(2, "big")


def decode_write_single_request(pdu: bytes | bytearray) -> tuple[int, int]:
    """Return (address, value).  The normal response is an echo of the request."""
    if len(pdu) != 5:
        raise IllegalDataValue(f"write-single request must be 5 bytes, got {len(pdu)}")
    if pdu[0] != FC_WRITE_SINGLE_REGISTER:
        raise IllegalFunction(f"expected 0x06, got 0x{pdu[0]:02x}")
    return int.from_bytes(pdu[1:3], "big"), int.from_bytes(pdu[3:5], "big")


# Write multiple registers (0x10) -------------------------------------------
def encode_write_multiple_request(address: int, values: Sequence[int]) -> bytes:
    quantity = len(values)
    if not 0 <= address <= 0xFFFF or not 1 <= quantity <= MAX_WRITE_MULTIPLE_QUANTITY:
        raise IllegalDataValue("address out of range or quantity not in 1..123")
    body = bytearray(
        bytes([FC_WRITE_MULTIPLE_REGISTERS])
        + address.to_bytes(2, "big")
        + quantity.to_bytes(2, "big")
        + bytes([quantity * 2])
    )
    for value in values:
        if not 0 <= value <= 0xFFFF:
            raise IllegalDataValue(f"register value 0x{value:x} does not fit in 16 bits")
        body += value.to_bytes(2, "big")
    return bytes(body)


def decode_write_multiple_request(pdu: bytes | bytearray) -> tuple[int, list[int]]:
    """Return (address, values)."""
    if len(pdu) < 6:
        raise IllegalDataValue("write-multiple request too short")
    if pdu[0] != FC_WRITE_MULTIPLE_REGISTERS:
        raise IllegalFunction(f"expected 0x10, got 0x{pdu[0]:02x}")
    address = int.from_bytes(pdu[1:3], "big")
    quantity = int.from_bytes(pdu[3:5], "big")
    byte_count = pdu[5]
    if not 1 <= quantity <= MAX_WRITE_MULTIPLE_QUANTITY:
        raise IllegalDataValue(f"write quantity {quantity} not in 1..123")
    if byte_count != quantity * 2:
        raise IllegalDataValue(
            f"byte count {byte_count} does not match quantity {quantity}"
        )
    if len(pdu) != 6 + byte_count:
        raise IllegalDataValue(
            f"frame length {len(pdu)} does not match byte count {byte_count}"
        )
    values = [int.from_bytes(pdu[i : i + 2], "big") for i in range(6, len(pdu), 2)]
    return address, values


def encode_write_multiple_response(address: int, quantity: int) -> bytes:
    if not 0 <= address <= 0xFFFF or not 1 <= quantity <= MAX_WRITE_MULTIPLE_QUANTITY:
        raise IllegalDataValue("address/quantity out of range")
    return (
        bytes([FC_WRITE_MULTIPLE_REGISTERS])
        + address.to_bytes(2, "big")
        + quantity.to_bytes(2, "big")
    )


def decode_write_multiple_response(pdu: bytes | bytearray) -> tuple[int, int]:
    if len(pdu) != 5:
        raise IllegalDataValue(f"write-multiple response must be 5 bytes, got {len(pdu)}")
    if pdu[0] != FC_WRITE_MULTIPLE_REGISTERS:
        raise IllegalFunction(f"expected 0x10, got 0x{pdu[0]:02x}")
    address = int.from_bytes(pdu[1:3], "big")
    quantity = int.from_bytes(pdu[3:5], "big")
    if not 1 <= quantity <= MAX_WRITE_MULTIPLE_QUANTITY:
        raise IllegalDataValue(f"quantity {quantity} not in 1..123")
    return address, quantity
