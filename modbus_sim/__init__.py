"""Local Modbus protocol codec and device simulator.

All data lives in process memory or local files. This package never connects
to real PLCs, serial ports, industrial devices or cloud services.
"""

from .crc16 import crc16, append_crc, check_crc
from .exceptions import ModbusError, IllegalFunctionError, IllegalAddressError
from .registers import Device, DeviceConfig, WordOrder, RegisterSpace
from .pdu import (
    FC_READ_HOLDING,
    FC_READ_INPUT,
    FC_WRITE_SINGLE,
    FC_WRITE_MULTIPLE,
    parse_request,
    execute,
    encode_exception,
)
from .mbap import MBAP, encode_mbap, TcpFramer
from .rtu import RtuFramer, encode_rtu, decode_rtu, RtuDecodeError
from .bus import DeviceBus
from .server import TcpModbusServer, RtuOverTcpServer
from .config import load_config, build_bus

__all__ = [
    "crc16",
    "append_crc",
    "check_crc",
    "ModbusError",
    "IllegalFunctionError",
    "IllegalAddressError",
    "Device",
    "DeviceConfig",
    "WordOrder",
    "RegisterSpace",
    "FC_READ_HOLDING",
    "FC_READ_INPUT",
    "FC_WRITE_SINGLE",
    "FC_WRITE_MULTIPLE",
    "parse_request",
    "execute",
    "encode_exception",
    "MBAP",
    "encode_mbap",
    "TcpFramer",
    "RtuFramer",
    "encode_rtu",
    "decode_rtu",
    "RtuDecodeError",
    "DeviceBus",
    "TcpModbusServer",
    "RtuOverTcpServer",
    "load_config",
    "build_bus",
]
