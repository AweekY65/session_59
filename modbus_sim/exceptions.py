"""Modbus exception codes and error types."""

# Standard Modbus exception codes (PDU data byte 2)
EX_ILLEGAL_FUNCTION = 0x01
EX_ILLEGAL_DATA_ADDRESS = 0x02
EX_ILLEGAL_DATA_VALUE = 0x03
EX_SLAVE_DEVICE_FAILURE = 0x04
EX_GATEWAY_PATH_UNAVAILABLE = 0x0A
EX_GATEWAY_TARGET_NO_RESPONSE = 0x0B


class ModbusError(Exception):
    """A Modbus protocol-level error carrying a standard exception code."""

    exception_code = EX_SLAVE_DEVICE_FAILURE

    def __init__(self, message: str = "", exception_code: int | None = None):
        super().__init__(message or self.__class__.__name__)
        if exception_code is not None:
            self.exception_code = exception_code


class IllegalFunctionError(ModbusError):
    exception_code = EX_ILLEGAL_FUNCTION


class IllegalAddressError(ModbusError):
    exception_code = EX_ILLEGAL_DATA_ADDRESS


class IllegalDataValueError(ModbusError):
    exception_code = EX_ILLEGAL_DATA_VALUE


class GatewayTargetNoResponseError(ModbusError):
    """Raised when a request names a unit id the gateway does not know.

    Real TCP gateways commonly answer with exception code 0x0B for an
    unknown unit id. It guarantees that a wrong/unknown unit can never
    reach another device's register space.
    """

    exception_code = EX_GATEWAY_TARGET_NO_RESPONSE
