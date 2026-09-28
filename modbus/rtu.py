"""Modbus RTU frame codec and a loopback RTU server.

RTU frame layout::

    [unit id (1)] [PDU (N)] [CRC low] [CRC high]

CRC is CRC-16/Modbus (see crc.py), appended low byte first.

Two ways to find frame boundaries exist on a real serial line: the 3.5-char
silent interval, or length prediction for function codes whose length is
known.  :class:`RTUFramer` supports both: :meth:`RTUFramer.feed` accumulates
arbitrary chunks (so half/coalesced frames are handled) and uses length
prediction to complete frames immediately; :meth:`RTUFramer.flush` finishes
the remaining buffer when the caller observes an idle gap.

:class:`RTUServer` is an RTU master/slave session tunneled over a **localhost
TCP socket** -- the bytes on the socket are RTU frames (unit id + PDU + CRC),
not Modbus TCP.  This keeps tests fully local with no serial hardware.
"""

from __future__ import annotations

import socket
import socketserver
import threading

from . import pdu
from .crc import crc16
from .device import DeviceMap

MIN_FRAME_LEN = 4  # unit id + function code + 2 CRC bytes
_MAX_PDU_LEN = 253


class CRCError(ValueError):
    """Raised when an RTU frame's CRC does not match its payload."""


def encode_frame(unit_id: int, pdu_bytes: bytes | bytearray) -> bytes:
    """Build an RTU frame: unit id + PDU + CRC (low byte first)."""
    if not 0 <= unit_id <= 247:
        raise ValueError("RTU unit id must be in 0..247 (0 = broadcast)")
    body = bytes([unit_id]) + bytes(pdu_bytes)
    crc = crc16(body)
    return body + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def decode_frame(frame: bytes | bytearray) -> tuple[int, bytes]:
    """Validate CRC and return (unit_id, pdu)."""
    if len(frame) < MIN_FRAME_LEN:
        raise ValueError(f"RTU frame too short: {len(frame)} bytes")
    payload = bytes(frame[:-2])
    crc_received = frame[-2] | (frame[-1] << 8)
    crc_calculated = crc16(payload)
    if crc_received != crc_calculated:
        raise CRCError(
            f"CRC error: received 0x{crc_received:04x}, "
            f"calculated 0x{crc_calculated:04x}"
        )
    return payload[0], payload[1:]


def _expected_len(role: str, buf: bytes | bytearray) -> int | None:
    """Predict total frame length from the leading bytes.

    ``role`` is ``"master"`` (buffer starts with a request) or ``"slave"``
    (buffer starts with a response).  Returns None when the function code is
    unknown or too few bytes are available to predict the length yet.
    """
    if len(buf) < 2:
        return None
    fc = buf[1]
    if fc & 0x80:  # exception responses are always 5 bytes
        return 5
    if fc in (pdu.FC_READ_HOLDING_REGISTERS, pdu.FC_READ_INPUT_REGISTERS):
        if role == "master":
            return 8  # unit + fc + addr(2) + qty(2) + crc(2)
        if len(buf) >= 3:
            # unit + fc + bytecount + data + crc
            return 3 + buf[2] + 2
        return None
    if fc == pdu.FC_WRITE_SINGLE_REGISTER:
        return 8
    if fc == pdu.FC_WRITE_MULTIPLE_REGISTERS:
        if role == "master":
            if len(buf) >= 7:
                # unit..bytecount (7) + data + crc
                return 7 + buf[6] + 2
            return None
        return 8  # write-multiple response: unit+fc+addr+qty+crc
    return None


class RTUFramer:
    """Reassemble RTU frames from arbitrary byte chunks.

    Usage:::

        framer = RTUFramer(role="master")
        for chunk in chunks_received_from_wire():
            for frame in framer.feed(chunk):
                process(frame)
        for frame in framer.flush():  # after an observed idle gap
            process(frame)
    """

    def __init__(self, role: str = "master"):
        if role not in ("master", "slave"):
            raise ValueError("role must be 'master' or 'slave'")
        self.role = role
        self._buf = bytearray()

    def feed(self, chunk: bytes | bytearray) -> list[bytes]:
        self._buf.extend(chunk)
        frames: list[bytes] = []
        while len(self._buf) >= MIN_FRAME_LEN:
            expected = _expected_len(self.role, self._buf)
            if expected is None:
                break  # unknown fc / waiting for more: flush on idle gap
            if expected > MIN_FRAME_LEN + _MAX_PDU_LEN:
                # Implausible length: garbage in the buffer; hand it to flush.
                break
            if len(self._buf) < expected:
                break  # half frame, wait for more bytes
            frames.append(bytes(self._buf[:expected]))
            del self._buf[:expected]
        return frames

    def flush(self) -> list[bytes]:
        """Emit whatever is left in the buffer (called at the 3.5-char gap)."""
        frames: list[bytes] = []
        if len(self._buf) >= MIN_FRAME_LEN:
            frames.append(bytes(self._buf))
        self._buf.clear()
        return frames


def serve_rtu_connection(
    sock: socket.socket,
    device_map: DeviceMap,
    crc_policy: str = "respond",
    idle_timeout: float = 0.2,
) -> None:
    """Service one RTU-over-socket connection (server side).

    ``crc_policy``:

    * ``"respond"`` (default): a frame with a bad CRC gets an exception
      response ``04 Slave Device Failure`` so the behaviour is testable and
      matches this project's stated requirements.
    * ``"drop"``: the behaviour of a real Modbus RTU slave -- the frame is
      silently discarded (nothing is written back).

    Requests addressed to an unknown unit id are always silently dropped,
    exactly as on a multidrop serial bus where only the addressed slave
    answers.

    ``idle_timeout`` emulates the RTU 3.5-character silent interval: when no
    byte arrives for that long, whatever is in the framer buffer is treated
    as a complete frame (this is the only way to terminate a frame whose
    function code is unknown and whose length cannot be predicted).
    """
    sock.settimeout(idle_timeout)
    framer = RTUFramer(role="master")

    def process(frame: bytes) -> None:
        try:
            unit_id, request_pdu = decode_frame(frame)
        except CRCError:
            if crc_policy == "respond":
                fc = frame[1] if len(frame) >= 2 else 0
                reply = encode_frame(
                    frame[0] if frame else 0,
                    pdu.exception_response(fc, pdu.EXN_SLAVE_DEVICE_FAILURE),
                )
                sock.sendall(reply)
            return
        except ValueError:
            return  # runt frame: real slaves ignore it
        if unit_id == 0:
            return  # broadcast: no response
        if not device_map.has(unit_id):
            return  # not our address: stay silent on the bus
        response_pdu = device_map.get(unit_id).handle_pdu(request_pdu)
        sock.sendall(encode_frame(unit_id, response_pdu))

    while True:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            for frame in framer.flush():
                process(frame)
            continue
        except (ConnectionError, OSError):
            break
        if not chunk:
            break
        for frame in framer.feed(chunk):
            process(frame)
    for frame in framer.flush():
        process(frame)


class _RTUHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        serve_rtu_connection(
            self.request,
            self.server.device_map,
            getattr(self.server, "crc_policy", "respond"),
        )


class RTUServer(socketserver.ThreadingTCPServer):
    """A multi-connection RTU server listening on localhost TCP.

    Bytes exchanged are raw RTU frames (with CRC), *not* Modbus TCP MBAP
    frames -- this exists purely to exercise the RTU codec end-to-end on a
    loopback socket without a serial port.
    """

    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        device_map: DeviceMap,
        host: str = "127.0.0.1",
        port: int = 0,
        crc_policy: str = "respond",
    ):
        self.device_map = device_map
        self.crc_policy = crc_policy
        super().__init__((host, port), _RTUHandler)

    @property
    def port(self) -> int:
        return self.server_address[1]

    def serve_forever_in_thread(self) -> threading.Thread:
        thread = threading.Thread(target=self.serve_forever, daemon=True)
        thread.start()
        return thread
