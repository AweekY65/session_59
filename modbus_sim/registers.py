"""Device model: independent register spaces per simulated unit id.

Register model
--------------
Every device owns two independent 16-bit register banks:

* **Holding registers** (function 0x03 read, 0x06/0x10 write), address 0..n-1
* **Input registers**   (function 0x04 read-only),        address 0..n-1

32-bit values occupy two consecutive 16-bit registers ("words").  Each
register is always transmitted big-endian on the wire (Modbus rule); the
order in which the *high* and *low* 16-bit word are placed is configurable.
"""

from __future__ import annotations

import struct
import threading
from enum import Enum

from .exceptions import (
    IllegalAddressError,
    IllegalDataValueError,
    IllegalFunctionError,
)

# Modbus hard limits
MAX_READ_REGS = 125
MAX_WRITE_REGS = 123


class WordOrder(str, Enum):
    """Where the most-significant 16-bit word of a 32-bit value lives.

    Register bytes are always big-endian on the wire.  These four orders
    name, in wire order, the four bytes of the 32-bit value:

    ====== ========= ================================================
    name   word/byte meaning
    ====== ========= ================================================
    ABCD   big       high word at lower address (most common default)
    BADC   mid-little swapped bytes inside each big-endian word
    CDAB   word-swap low word at lower address (common PLC setting)
    DCBA  little    low word at lower address, little-endian bytes
    ====== ========= ================================================
    """

    ABCD = "ABCD"  # big-endian:        addr=N -> hi word
    BADC = "BADC"  # byte-swapped big
    CDAB = "CDAB"  # word-swapped:      addr=N -> lo word, big-endian bytes
    DCBA = "DCBA"  # full little-endian

    @property
    def high_word_first(self) -> bool:
        """True when the high 16-bit word sits at the lower address."""
        return self in (WordOrder.ABCD, WordOrder.BADC)

    @property
    def word_byte_order(self) -> str:
        """'big'/'little' byte order inside each 16-bit wire word."""
        return "big" if self in (WordOrder.ABCD, WordOrder.CDAB) else "little"


# Permutation table: how wire bytes [b0,b1,b2,b3] map to the native
# big-endian 32-bit value bytes ABCD.
_WIRE_TO_VALUE = {
    WordOrder.ABCD: (0, 1, 2, 3),
    WordOrder.BADC: (1, 0, 3, 2),
    WordOrder.CDAB: (2, 3, 0, 1),
    WordOrder.DCBA: (3, 2, 1, 0),
}


class RegisterSpace:
    """A flat array of 16-bit registers guarded by a re-entrant lock."""

    def __init__(self, size: int, readonly: bool, values=None):
        if not 1 <= size <= 0x10000:
            raise ValueError("register space size out of range")
        self.size = size
        self.readonly = readonly
        self._regs = [0] * size
        if values:
            for addr, value in dict(values).items():
                self._check_addr(addr)
                self._regs[addr] = value & 0xFFFF
        self.lock = threading.RLock()

    def _check_addr(self, address: int) -> None:
        if not 0 <= address < self.size:
            raise IllegalAddressError(f"address {address} out of range [0,{self.size})")

    def read(self, address: int, count: int) -> list[int]:
        with self.lock:
            self._check_addr(address)
            if count < 1 or address + count > self.size:
                raise IllegalAddressError(
                    f"read {address}+{count} exceeds space size {self.size}"
                )
            return list(self._regs[address : address + count])

    def write(self, address: int, values) -> None:
        values = list(values)
        with self.lock:
            if self.readonly:
                raise IllegalFunctionError("register space is read-only")
            self._check_addr(address)
            if not values or address + len(values) > self.size:
                raise IllegalAddressError(
                    f"write {address}+{len(values)} exceeds space size {self.size}"
                )
            for v in values:
                if not 0 <= v <= 0xFFFF:
                    raise IllegalDataValueError(f"register value {v} not 16-bit")
            self._regs[address : address + len(values)] = values


class DeviceConfig:
    """Static configuration for one simulated unit."""

    def __init__(
        self,
        unit_id: int,
        holding_size: int = 100,
        input_size: int = 100,
        word_order: WordOrder | str = WordOrder.ABCD,
        holding=None,
        inputs=None,
    ):
        if not 0 <= unit_id <= 247:
            raise ValueError("unit id must be 0..247")
        self.unit_id = unit_id
        self.holding_size = holding_size
        self.input_size = input_size
        self.word_order = WordOrder(word_order)
        self.holding = holding or {}
        self.inputs = inputs or {}


class Device:
    """A simulated Modbus unit with independent register spaces."""

    def __init__(self, config: DeviceConfig):
        self.unit_id = config.unit_id
        self.word_order = config.word_order
        self.holding = RegisterSpace(
            config.holding_size, readonly=False, values=config.holding
        )
        self.input = RegisterSpace(
            config.input_size, readonly=True, values=config.inputs
        )

    # ---- 16-bit access -------------------------------------------------

    def read_holding(self, address: int, count: int) -> list[int]:
        return self.holding.read(address, count)

    def read_input(self, address: int, count: int) -> list[int]:
        return self.input.read(address, count)

    def write_registers(self, address: int, values) -> None:
        self.holding.write(address, values)

    # ---- 32-bit helpers -------------------------------------------------

    def read_u32(self, address: int) -> int:
        """Read an unsigned 32-bit value spanning registers address/+1."""
        words = self.holding.read(address, 2)
        return _words_to_u32(words, self.word_order)

    def write_u32(self, address: int, value: int) -> None:
        if not 0 <= value <= 0xFFFFFFFF:
            raise IllegalDataValueError(f"u32 value {value} out of range")
        self.holding.write(address, _u32_to_words(value, self.word_order))

    def read_i32(self, address: int) -> int:
        value = self.read_u32(address)
        return value - 0x100000000 if value & 0x80000000 else value

    def read_float32(self, address: int) -> float:
        return struct.unpack(">f", struct.pack(">I", self.read_u32(address)))[0]

    def write_float32(self, address: int, value: float) -> None:
        self.write_u32(address, struct.unpack(">I", struct.pack(">f", value))[0])


def _words_to_u32(words: list[int], order: WordOrder) -> int:
    wire = bytes(
        (
            (words[0] >> 8) & 0xFF,
            words[0] & 0xFF,
            (words[1] >> 8) & 0xFF,
            words[1] & 0xFF,
        )
    )
    perm = _WIRE_TO_VALUE[order]
    value_bytes = bytes(wire[perm.index(i)] for i in range(4))
    return struct.unpack(">I", value_bytes)[0]


def _u32_to_words(value: int, order: WordOrder) -> list[int]:
    abcd = struct.pack(">I", value)  # native big-endian value bytes A B C D
    perm = _WIRE_TO_VALUE[order]
    wire = bytes(abcd[perm[i]] for i in range(4))
    return [(wire[0] << 8) | wire[1], (wire[2] << 8) | wire[3]]
