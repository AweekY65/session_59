"""Modbus PDU (function-code) encode/decode and execution.

Supported function codes
------------------------
0x03 read holding registers
0x04 read input registers
0x06 write single register
0x10 write multiple registers

PDU layout (addresses and counts are big-endian):

* read request:  fc, addr(2), qty(2)
* read response: fc, bytecount(1), data(qty*2)
* write single req/resp (echo): fc, addr(2), value(2)
* write multiple request:  fc, addr(2), qty(2), bytecount(1), values(qty*2)
* write multiple response: fc, addr(2), qty(2)
* exception: fc | 0x80, exception_code
"""

from __future__ import annotations

from dataclasses import dataclass

from .exceptions import (
    IllegalAddressError,
    IllegalDataValueError,
    IllegalFunctionError,
    ModbusError,
)

FC_READ_HOLDING = 0x03
FC_READ_INPUT = 0x04
FC_WRITE_SINGLE = 0x06
FC_WRITE_MULTIPLE = 0x10

SUPPORTED_FUNCTIONS = (FC_READ_HOLDING, FC_READ_INPUT, FC_WRITE_SINGLE, FC_WRITE_MULTIPLE)


@dataclass
class PduRequest:
    function: int
    address: int = 0
    quantity: int = 0
    values: tuple[int, ...] = ()


def encode_exception(function: int, exception_code: int) -> bytes:
    return bytes((function | 0x80, exception_code & 0xFF))


def parse_request(pdu: bytes) -> PduRequest:
    """Validate and decode a request PDU.

    Raises ModbusError subclasses for malformed PDUs (length/value errors),
    IllegalFunctionError for unsupported function codes.
    """
    if not pdu:
        raise IllegalDataValueError("empty PDU")
    fc = pdu[0]
    if fc not in SUPPORTED_FUNCTIONS:
        raise IllegalFunctionError(f"unsupported function code 0x{fc:02X}")

    if fc in (FC_READ_HOLDING, FC_READ_INPUT):
        if len(pdu) != 5:
            raise IllegalDataValueError("read request PDU must be 5 bytes")
        address, quantity = int.from_bytes(pdu[1:3], "big"), int.from_bytes(
            pdu[3:5], "big"
        )
        if not 1 <= quantity <= 125:
            raise IllegalDataValueError(f"read quantity {quantity} not in 1..125")
        return PduRequest(fc, address, quantity)

    if fc == FC_WRITE_SINGLE:
        if len(pdu) != 5:
            raise IllegalDataValueError("write-single request PDU must be 5 bytes")
        address = int.from_bytes(pdu[1:3], "big")
        return PduRequest(fc, address, 1, (int.from_bytes(pdu[3:5], "big"),))

    # write multiple
    if len(pdu) < 6:
        raise IllegalDataValueError("write-multiple request too short")
    address, quantity = int.from_bytes(pdu[1:3], "big"), int.from_bytes(pdu[3:5], "big")
    byte_count = pdu[5]
    if not 1 <= quantity <= 123:
        raise IllegalDataValueError(f"write quantity {quantity} not in 1..123")
    if byte_count != quantity * 2:
        raise IllegalDataValueError(
            f"byte count {byte_count} != 2*quantity {quantity * 2}"
        )
    if len(pdu) != 6 + byte_count:
        raise IllegalDataValueError(
            f"write-multiple payload {len(pdu) - 6} bytes != {byte_count}"
        )
    values = tuple(
        int.from_bytes(pdu[6 + 2 * i : 8 + 2 * i], "big") for i in range(quantity)
    )
    return PduRequest(fc, address, quantity, values)


def execute(device, request: PduRequest) -> bytes:
    """Apply *request* to a :class:`Device` and return the response PDU.

    Address-range validation happens here against the concrete device's
    register spaces; malformed-PDU validation happens in parse_request.
    """
    fc = request.function

    if fc in (FC_READ_HOLDING, FC_READ_INPUT):
        space = device.holding if fc == FC_READ_HOLDING else device.input
        if request.address + request.quantity > space.size:
            raise IllegalAddressError(
                f"address {request.address}+{request.quantity} over size {space.size}"
            )
        values = space.read(request.address, request.quantity)
        body = b"".join(v.to_bytes(2, "big") for v in values)
        return bytes((fc, len(body))) + body

    if fc == FC_WRITE_SINGLE:
        if request.address >= device.holding.size:
            raise IllegalAddressError(f"address {request.address} out of range")
        device.write_registers(request.address, request.values)
        return (
            bytes((fc,))
            + request.address.to_bytes(2, "big")
            + request.values[0].to_bytes(2, "big")
        )

    if fc == FC_WRITE_MULTIPLE:
        if request.address + request.quantity > device.holding.size:
            raise IllegalAddressError(
                f"address {request.address}+{request.quantity} over holding size"
            )
        device.write_registers(request.address, request.values)
        return (
            bytes((fc,))
            + request.address.to_bytes(2, "big")
            + request.quantity.to_bytes(2, "big")
        )

    raise IllegalFunctionError(f"unsupported function 0x{fc:02X}")


def handle_pdu(device, pdu: bytes) -> bytes:
    """Parse and execute *pdu*; on ModbusError return an exception PDU."""
    try:
        return execute(device, parse_request(pdu))
    except ModbusError as exc:
        return encode_exception(pdu[0] if pdu else 0, exc.exception_code)
