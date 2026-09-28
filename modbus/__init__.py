"""Local Modbus protocol codec and device simulator.

Everything in this package is self-contained: registers live in memory or in
local JSON files, and the only network transport used is a loopback TCP
server (127.0.0.1).  No PLC, serial port or cloud service is ever contacted.
"""

from .crc import crc16
from .device import (
    DeviceMap,
    ModbusDevice,
    WordOrder,
    load_devices,
)
from .pdu import (
    FC_READ_HOLDING_REGISTERS,
    FC_READ_INPUT_REGISTERS,
    FC_WRITE_SINGLE_REGISTER,
    FC_WRITE_MULTIPLE_REGISTERS,
    EXN_ILLEGAL_FUNCTION,
    EXN_ILLEGAL_DATA_ADDRESS,
    EXN_ILLEGAL_DATA_VALUE,
    EXN_SLAVE_DEVICE_FAILURE,
    EXN_GATEWAY_TARGET_FAILED,
    IllegalFunction,
    IllegalDataAddress,
    IllegalDataValue,
    ModbusError,
    exception_response,
)
from .rtu import CRCError, RTUFramer, RTUServer, decode_frame, encode_frame
from .tcp import TCPFramer, TCPServer, encode_mbap

__all__ = [
    "crc16",
    "DeviceMap",
    "ModbusDevice",
    "WordOrder",
    "load_devices",
    "FC_READ_HOLDING_REGISTERS",
    "FC_READ_INPUT_REGISTERS",
    "FC_WRITE_SINGLE_REGISTER",
    "FC_WRITE_MULTIPLE_REGISTERS",
    "EXN_ILLEGAL_FUNCTION",
    "EXN_ILLEGAL_DATA_ADDRESS",
    "EXN_ILLEGAL_DATA_VALUE",
    "EXN_SLAVE_DEVICE_FAILURE",
    "EXN_GATEWAY_TARGET_FAILED",
    "IllegalFunction",
    "IllegalDataAddress",
    "IllegalDataValue",
    "ModbusError",
    "exception_response",
    "CRCError",
    "RTUFramer",
    "RTUServer",
    "decode_frame",
    "encode_frame",
    "TCPFramer",
    "TCPServer",
    "encode_mbap",
]
