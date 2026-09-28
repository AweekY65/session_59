"""Simulated Modbus devices with independent register spaces.

Register model
--------------
Each device (unit id) owns two 16-bit register banks:

* **Holding registers** (FC 0x03 read / 0x06, 0x10 write), 0-based addresses.
* **Input registers** (FC 0x04 read only), 0-based addresses.

Registers are plain Python ``int`` values in 0..65535.  Banks default to
``size`` addresses starting at 0 and are stored sparsely, so a size of 65536
costs almost nothing.

Byte / word order
-----------------
Every register is transmitted big-endian, as Modbus requires: value
``0x1234`` is wire bytes ``12 34``.  A 32-bit value occupies two registers and
the order of the two 16-bit words is configurable:

* ``WordOrder.HIGH_WORD_FIRST`` ("ABCD", Modbus default / big-endian word
  order): ``0x12345678`` -> registers ``[0x1234, 0x5678]``.
* ``WordOrder.LOW_WORD_FIRST`` ("DCBA", little-endian word order):
  ``0x12345678`` -> registers ``[0x5678, 0x1234]``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Union

from . import pdu

WordOrderValue = Union[str, "WordOrder"]


class WordOrder:
    """Order of the two 16-bit words that make up a 32-bit register value."""

    HIGH_WORD_FIRST: "WordOrder"
    LOW_WORD_FIRST: "WordOrder"

    def __init__(self, name: str, high_first: bool):
        self.name = name
        self.high_first = high_first

    def __repr__(self) -> str:
        return f"WordOrder.{self.name}"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, WordOrder) and other.high_first == self.high_first

    def __hash__(self) -> int:
        return hash(self.high_first)

    @staticmethod
    def parse(value: WordOrderValue) -> "WordOrder":
        if isinstance(value, WordOrder):
            return value
        key = str(value).strip().lower().replace("-", "_")
        if key in ("high", "high_first", "high_word_first", "abcd", "big", "big_endian"):
            return WordOrder.HIGH_WORD_FIRST
        if key in ("low", "low_first", "low_word_first", "dcba", "little", "little_endian"):
            return WordOrder.LOW_WORD_FIRST
        raise ValueError(f"unknown word order: {value!r}")


WordOrder.HIGH_WORD_FIRST = WordOrder("HIGH_WORD_FIRST", True)
WordOrder.LOW_WORD_FIRST = WordOrder("LOW_WORD_FIRST", False)


class ModbusDevice:
    """One simulated slave: an independent pair of register banks."""

    def __init__(
        self,
        unit_id: int,
        size: int = 65536,
        input_size: int | None = None,
        word_order: WordOrderValue = WordOrder.HIGH_WORD_FIRST,
    ):
        if not 1 <= int(unit_id) <= 247:
            raise ValueError("unit id must be in 1..247")
        if not 1 <= size <= 65536 or not 1 <= (input_size or size) <= 65536:
            raise ValueError("register bank size must be in 1..65536")
        self.unit_id = int(unit_id)
        self.size = size
        self.input_size = input_size if input_size is not None else size
        self.word_order = WordOrder.parse(word_order)
        # Sparse storage: address -> 16-bit value.  Missing addresses read 0.
        self._holding: dict[int, int] = {}
        self._input: dict[int, int] = {}

    # -- 16-bit access ------------------------------------------------------
    def _check_range(self, address: int, quantity: int, size: int) -> None:
        if not 0 <= address < size:
            raise pdu.IllegalDataAddress(f"starting address {address} out of range")
        if quantity < 1 or address + quantity > size:
            raise pdu.IllegalDataAddress(
                f"range {address}..{address + quantity - 1} exceeds bank size {size}"
            )

    def read_holding(self, address: int, quantity: int) -> list[int]:
        self._check_range(address, quantity, self.size)
        return [self._holding.get(address + i, 0) for i in range(quantity)]

    def read_input(self, address: int, quantity: int) -> list[int]:
        self._check_range(address, quantity, self.input_size)
        return [self._input.get(address + i, 0) for i in range(quantity)]

    def write_single(self, address: int, value: int) -> None:
        if not 0 <= address < self.size:
            raise pdu.IllegalDataAddress(f"address {address} out of range")
        if not 0 <= value <= 0xFFFF:
            raise pdu.IllegalDataValue(f"value 0x{value:x} does not fit in 16 bits")
        self._holding[address] = value

    def write_multiple(self, address: int, values: list[int]) -> None:
        self._check_range(address, len(values), self.size)
        for i, value in enumerate(values):
            if not 0 <= value <= 0xFFFF:
                raise pdu.IllegalDataValue(f"value 0x{value:x} does not fit in 16 bits")
            self._holding[address + i] = value

    # -- 32-bit access ------------------------------------------------------
    def read_u32(self, address: int, word_order: WordOrderValue | None = None) -> int:
        order = WordOrder.parse(word_order) if word_order is not None else self.word_order
        words = self.read_holding(address, 2)
        high, low = (words[0], words[1]) if order.high_first else (words[1], words[0])
        return (high << 16) | low

    def write_u32(
        self, address: int, value: int, word_order: WordOrderValue | None = None
    ) -> None:
        if not 0 <= value <= 0xFFFFFFFF:
            raise ValueError("value does not fit in unsigned 32 bits")
        order = WordOrder.parse(word_order) if word_order is not None else self.word_order
        high, low = (value >> 16) & 0xFFFF, value & 0xFFFF
        words = [high, low] if order.high_first else [low, high]
        self.write_multiple(address, words)

    def read_i32(self, address: int, word_order: WordOrderValue | None = None) -> int:
        value = self.read_u32(address, word_order)
        return value - 0x100000000 if value & 0x80000000 else value

    def write_i32(
        self, address: int, value: int, word_order: WordOrderValue | None = None
    ) -> None:
        if not -(0x80000000) <= value <= 0x7FFFFFFF:
            raise ValueError("value does not fit in signed 32 bits")
        self.write_u32(address, value & 0xFFFFFFFF, word_order)

    # -- PDU dispatch -------------------------------------------------------
    def handle_pdu(self, pdu_bytes: bytes | bytearray) -> bytes:
        """Process a request PDU and return the response PDU.

        Any :class:`ModbusError` is converted to an exception response; any
        other exception becomes ``04 Slave Device Failure``.
        """
        if not pdu_bytes:
            return pdu.exception_response(0, pdu.EXN_SLAVE_DEVICE_FAILURE)
        fc = pdu_bytes[0]
        try:
            if fc in (pdu.FC_READ_HOLDING_REGISTERS, pdu.FC_READ_INPUT_REGISTERS):
                _, address, quantity = pdu.decode_read_request(pdu_bytes)
                if fc == pdu.FC_READ_HOLDING_REGISTERS:
                    values = self.read_holding(address, quantity)
                else:
                    values = self.read_input(address, quantity)
                return pdu.encode_read_response(fc, values)
            if fc == pdu.FC_WRITE_SINGLE_REGISTER:
                address, value = pdu.decode_write_single_request(pdu_bytes)
                self.write_single(address, value)
                return bytes(pdu_bytes)  # normal response echoes the request
            if fc == pdu.FC_WRITE_MULTIPLE_REGISTERS:
                address, values = pdu.decode_write_multiple_request(pdu_bytes)
                self.write_multiple(address, values)
                return pdu.encode_write_multiple_response(address, len(values))
            raise pdu.IllegalFunction(f"unsupported function code 0x{fc:02x}")
        except pdu.ModbusError as exc:
            return pdu.exception_response(fc, exc.code)
        except Exception:  # pragma: no cover - defensive catch-all
            return pdu.exception_response(fc, pdu.EXN_SLAVE_DEVICE_FAILURE)


class DeviceMap:
    """A collection of simulated devices keyed by unit id.

    Devices are strictly isolated: looking up a unit id that was never
    registered raises :class:`GatewayTargetFailed`, and no device can read or
    write another device's registers.
    """

    def __init__(self, devices: list[ModbusDevice] | None = None):
        self._devices: dict[int, ModbusDevice] = {}
        for device in devices or []:
            self.add(device)

    def add(self, device: ModbusDevice) -> None:
        if device.unit_id in self._devices:
            raise ValueError(f"duplicate unit id {device.unit_id}")
        self._devices[device.unit_id] = device

    def get(self, unit_id: int) -> ModbusDevice:
        try:
            return self._devices[unit_id]
        except KeyError:
            raise pdu.GatewayTargetFailed(f"unknown unit id {unit_id}") from None

    def has(self, unit_id: int) -> bool:
        return unit_id in self._devices

    def unit_ids(self) -> list[int]:
        return sorted(self._devices)

    def handle(self, unit_id: int, pdu_bytes: bytes | bytearray) -> bytes:
        """Dispatch a PDU to the addressed device.

        Unknown unit ids (TCP) receive ``0B Gateway Target Device Failed``.
        RTU code paths should instead drop the frame silently, which is the
        behaviour of real serial slaves; see rtu.serve_rtu_connection.
        """
        try:
            device = self.get(unit_id)
        except pdu.GatewayTargetFailed as exc:
            fc = pdu_bytes[0] if pdu_bytes else 0
            return pdu.exception_response(fc, exc.code)
        return device.handle_pdu(pdu_bytes)


def load_devices(config_path: str | Path) -> DeviceMap:
    """Load devices and their register contents from a local JSON file.

    Schema (all keys except ``unit_id`` optional)::

        {
          "devices": [
            {
              "unit_id": 1,
              "size": 100,
              "input_size": 100,
              "word_order": "high_word_first",
              "holding": {"0": 1234, "2": [0, 1]},
              "input":   {"10": [5, 6]}
            }
          ]
        }

    Register values may be a single integer or a list of integers; the list
    starts at the given address.
    """
    path = Path(config_path)
    config = json.loads(path.read_text(encoding="utf-8"))
    device_map = DeviceMap()
    for spec in config.get("devices", []):
        device = ModbusDevice(
            unit_id=spec["unit_id"],
            size=spec.get("size", 65536),
            input_size=spec.get("input_size"),
            word_order=spec.get("word_order", WordOrder.HIGH_WORD_FIRST),
        )
        for address, value in spec.get("holding", {}).items():
            values = value if isinstance(value, list) else [value]
            device.write_multiple(int(address), [int(v) for v in values])
        for address, value in spec.get("input", {}).items():
            values = value if isinstance(value, list) else [value]
            addr = int(address)
            for offset, raw in enumerate(values):
                v = int(raw)
                device._check_range(addr + offset, 1, device.input_size)
                if not 0 <= v <= 0xFFFF:
                    raise ValueError("input value out of 16-bit range")
                device._input[addr + offset] = v
        device_map.add(device)
    return device_map
