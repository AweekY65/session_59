"""Local Modbus servers (TCP and RTU-over-localhost-socket).

No real serial ports or PLCs: RTU frames are carried over a localhost TCP
socket purely so tests can exercise the RTU codec end to end.

TCP server behaviour
--------------------
* MBAP length drives reassembly (half packets / back-to-back frames OK)
* unknown unit id -> exception response 0x0B
* malformed MBAP stream -> connection closed

RTU server behaviour
--------------------
* CRC correct  -> PDU dispatched
* CRC wrong    -> frame dropped silently (Modbus spec default);
                  set ``crc_error_response=True`` to answer with an
                  exception frame using function code 0x00 and code 0x04
                  (useful for deterministic testing)
* unknown function (CRC valid) -> normal exception 0x01
"""

from __future__ import annotations

import socketserver

from .exceptions import ModbusError, EX_SLAVE_DEVICE_FAILURE
from .mbap import TcpFramer, FrameFormatError, encode_mbap
from .pdu import handle_pdu, parse_request
from .rtu import RtuFramer, decode_rtu, encode_rtu, RtuDecodeError


class _TcpHandler(socketserver.BaseRequestHandler):
    def handle(self):
        server: "TcpModbusServer" = self.server
        framer = TcpFramer()
        while True:
            data = self.request.recv(4096)
            if not data:
                return
            try:
                frames = framer.feed(data)
            except FrameFormatError:
                return  # not a Modbus TCP stream; close the connection
            for frame in frames:
                self.request.sendall(server.dispatch_tcp(frame))


class TcpModbusServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, bus, host: str = "127.0.0.1", port: int = 0):
        self.bus = bus
        super().__init__((host, port), _TcpHandler)

    @property
    def address(self):
        return self.server_address[0]

    @property
    def port(self) -> int:
        return self.server_address[1]

    def dispatch_tcp(self, frame) -> bytes:
        pdu = frame.pdu
        try:
            device = self.bus.get(frame.unit_id)
            response_pdu = handle_pdu(device, pdu)
        except ModbusError as exc:
            fc = pdu[0] if pdu else 0
            response_pdu = bytes((fc | 0x80, exc.exception_code))
        return encode_mbap(frame.transaction_id, frame.unit_id, response_pdu)


class _RtuHandler(socketserver.BaseRequestHandler):
    def handle(self):
        server: "RtuOverTcpServer" = self.server
        framer = RtuFramer(expect_response=False)
        self.request.settimeout(server.frame_gap_timeout)
        while True:
            try:
                data = self.request.recv(4096)
            except TimeoutError:
                # Serial idle gap: flush any residual bytes as one frame.
                tail = framer.end_stream()
                if tail and not self._process(tail):
                    return
                continue
            except OSError:
                return
            if not data:
                tail = framer.end_stream()
                if tail:
                    self._process(tail)
                return
            for raw in framer.feed(data):
                if not self._process(raw):
                    return

    def _process(self, raw: bytes) -> bool:
        """Handle one candidate frame. Return False to close the socket."""
        server: "RtuOverTcpServer" = self.server
        try:
            frame = decode_rtu(raw)
        except RtuDecodeError:
            if not server.crc_error_response:
                return True  # Modbus RTU spec: silently ignore bad CRC
            # Deterministic test hook: reply with a slave-device-failure
            # exception using function code 0x00 (no function was decoded).
            self.request.sendall(encode_rtu(raw[0], bytes((0x80, 0x04))))
            return True

        if frame.unit_id == 0:
            return True  # broadcast: accepted, no response

        if not server.bus.has(frame.unit_id):
            # RTU: a frame addressed to another slave is ignored entirely.
            return True

        device = server.bus.get(frame.unit_id)
        try:
            request = parse_request(frame.pdu)
        except ModbusError as exc:
            fc = frame.pdu[0] if frame.pdu else 0
            response_pdu = bytes((fc | 0x80, exc.exception_code))
        else:
            try:
                from .pdu import execute

                response_pdu = execute(device, request)
            except ModbusError as exc:
                response_pdu = bytes(
                    (request.function | 0x80, exc.exception_code)
                )
        self.request.sendall(encode_rtu(frame.unit_id, response_pdu))
        return True


class RtuOverTcpServer(socketserver.ThreadingTCPServer):
    """Serves RTU frames (address + PDU + CRC16) over a localhost socket."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        bus,
        host: str = "127.0.0.1",
        port: int = 0,
        crc_error_response: bool = False,
        frame_gap_timeout: float = 0.25,
    ):
        self.bus = bus
        self.crc_error_response = crc_error_response
        self.frame_gap_timeout = frame_gap_timeout
        super().__init__((host, port), _RtuHandler)

    @property
    def port(self) -> int:
        return self.server_address[1]


def crc_exception_frame(unit_id: int) -> bytes:
    """Build the optional bad-CRC exception reply (fc 0x00, code 0x04)."""
    return encode_rtu(unit_id, bytes((0x80, EX_SLAVE_DEVICE_FAILURE)))
