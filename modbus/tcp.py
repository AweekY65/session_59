"""Modbus TCP codec and a localhost-only simulator server.

TCP ADU layout (MODBUS Messaging on TCP/IP V1.0b, §3.1.1)::

    0     1     2     3     4     5     6     7   ...
    +-----------+-----------+----------+----------+------+
    | Transaction  | Protocol | Length   | Unit     | PDU  |
    | identifier    | id = 0   | (bytes)  | id       |      |
    +-----------+-----------+----------+----------+------+
      2 bytes       2 bytes    2 bytes    1 byte     N

* Transaction id is echoed by the server.
* Protocol id must be 0 for Modbus.
* Length counts unit id + PDU (it does NOT cover transaction id, protocol id
  or itself); its valid range is 1..256 but PDU is capped at 253 here.
* Unit id selects the simulated device; each unit has an isolated register
  space (see device.DeviceMap).  An unknown unit id produces exception
  ``0B Gateway Target Device Failed To Respond``.

TCP is a stream, so a single ``recv`` may contain several frames glued
together (粘包) or only part of one (半包).  :class:`TCPFramer` uses the
Length field to reassemble exactly -- code must never assume one read equals
one frame.
"""

from __future__ import annotations

import socketserver
import struct
import threading

from .device import DeviceMap

MBAP_HEADER_LEN = 7


def encode_mbap(transaction_id: int, unit_id: int, pdu: bytes | bytearray) -> bytes:
    """Wrap a PDU in a Modbus TCP ADU (MBAP header + PDU)."""
    if not 0 <= transaction_id <= 0xFFFF:
        raise ValueError("transaction id out of 16-bit range")
    if not 0 <= unit_id <= 0xFF:
        raise ValueError("unit id out of byte range")
    length = len(pdu) + 1
    if length > 256:
        raise ValueError("PDU too long for one Modbus TCP frame")
    return struct.pack(">HHH B", transaction_id, 0, length, unit_id) + bytes(pdu)


def decode_mbap(adu: bytes | bytearray) -> tuple[int, int, bytes]:
    """Parse one complete ADU; return (transaction_id, unit_id, pdu).

    Raises ValueError on malformed length/protocol fields.
    """
    if len(adu) < MBAP_HEADER_LEN + 1:
        raise ValueError("ADU shorter than MBAP header + 1 byte")
    transaction_id, protocol_id, length, unit_id = struct.unpack(
        ">HHH B", bytes(adu[:7])
    )
    if protocol_id != 0:
        raise ValueError(f"protocol id must be 0, got {protocol_id}")
    if length < 1 or length > 256:
        raise ValueError(f"length field {length} out of range 1..256")
    if len(adu) != MBAP_HEADER_LEN - 1 + length:
        raise ValueError(
            f"declared length {length} does not match actual {len(adu) - 6}"
        )
    return transaction_id, unit_id, bytes(adu[7:])


class TCPFramer:
    """Reassemble Modbus TCP ADUs from arbitrary stream chunks.

    Feed every ``recv`` result to :meth:`feed`; it yields zero or more
    complete ADUs.  Partial frames stay buffered until the rest arrives.
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, chunk: bytes | bytearray) -> list[bytes]:
        self._buf.extend(chunk)
        frames: list[bytes] = []
        while len(self._buf) >= MBAP_HEADER_LEN:
            length = int.from_bytes(self._buf[4:6], "big")
            if length < 1 or length > 256:
                # The stream cannot be resynchronized by guessing; fail loud.
                bad = length
                self._buf.clear()
                raise ValueError(f"invalid MBAP length field {bad}")
            total = MBAP_HEADER_LEN - 1 + length
            if len(self._buf) < total:
                break  # half packet: keep waiting
            frames.append(bytes(self._buf[:total]))
            del self._buf[:total]
        return frames

    def buffered(self) -> int:
        """Bytes of a partial frame currently held."""
        return len(self._buf)


def _handle_connection(device_map: DeviceMap, sock) -> None:
    framer = TCPFramer()
    while True:
        try:
            chunk = sock.recv(4096)
        except (ConnectionError, OSError):
            break
        if not chunk:
            break
        try:
            frames = framer.feed(chunk)
        except ValueError:
            break  # unrecoverable framing error; close the connection
        for adu in frames:
            transaction_id, protocol_id, length, unit_id = struct.unpack(
                ">HHH B", adu[:7]
            )
            if protocol_id != 0:
                # 0x04 is the closest standard code for a malformed request.
                from . import pdu

                response_pdu = pdu.exception_response(
                    adu[7] if len(adu) > 7 else 0, pdu.EXN_SLAVE_DEVICE_FAILURE
                )
            else:
                request_pdu = adu[7:]
                response_pdu = device_map.handle(unit_id, request_pdu)
            sock.sendall(encode_mbap(transaction_id, unit_id, response_pdu))


class _TCPHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        _handle_connection(self.server.device_map, self.request)


class TCPServer(socketserver.ThreadingTCPServer):
    """Multi-connection Modbus TCP simulator bound to localhost.

    The default host is 127.0.0.1: the simulator is local-only and never
    reaches a real PLC or any external host.
    """

    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        device_map: DeviceMap,
        host: str = "127.0.0.1",
        port: int = 5020,
    ):
        self.device_map = device_map
        super().__init__((host, port), _TCPHandler)

    @property
    def port(self) -> int:
        return self.server_address[1]

    def serve_forever_in_thread(self) -> threading.Thread:
        thread = threading.Thread(target=self.serve_forever, daemon=True)
        thread.start()
        return thread
