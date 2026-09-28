#!/usr/bin/env python3
"""Run the local Modbus simulator.

Examples
--------
Start a TCP server with the demo devices::

    python -m modbus_sim.cli tcp --config examples/devices.json --port 1502

Start an RTU-over-localhost server (no real serial port)::

    python -m modbus_sim.cli rtu --config examples/devices.json --port 1503

Run a quick self-test roundtrip in-process::

    python -m modbus_sim.cli demo
"""

from __future__ import annotations

import argparse
import sys
import time

from .config import build_bus, load_config
from .server import RtuOverTcpServer, TcpModbusServer


def cmd_tcp(args) -> int:
    bus = build_bus(load_config(args.config))
    server = TcpModbusServer(bus, host=args.host, port=args.port)
    print(f"Modbus TCP simulator on {args.host}:{server.port}; units {bus.unit_ids()}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        server.shutdown()
        server.server_close()
    return 0


def cmd_rtu(args) -> int:
    bus = build_bus(load_config(args.config))
    server = RtuOverTcpServer(
        bus,
        host=args.host,
        port=args.port,
        crc_error_response=args.crc_error_response,
    )
    print(
        f"Modbus RTU-over-localhost simulator on {args.host}:{server.port}; "
        f"units {bus.unit_ids()} (no real serial port)"
    )
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        server.shutdown()
        server.server_close()
    return 0


def cmd_demo(_args) -> int:
    from .client import TcpClient
    from .registers import DeviceConfig, WordOrder

    bus = build_bus(
        [
            DeviceConfig(1, holding_size=20, input_size=10,
                         holding={0: 1111, 2: 0, 3: 1234},
                         inputs={0: 9999}),
            DeviceConfig(2, holding_size=20, input_size=10,
                         word_order=WordOrder.CDAB),
        ]
    )
    server = TcpModbusServer(bus)
    import threading

    threading.Thread(target=server.serve_forever, daemon=True).start()
    with TcpClient(port=server.port) as client:
        print("unit1 holding[0..2]:", client.read_holding(1, 0, 3))
        print("unit1 input[0]:", client.read_input(1, 0, 1))
        print("write 0x06:", client.write_single(1, 1, 4242))
        print("write 0x10:", client.write_multiple(2, 0, [1, 2, 3]))
        print("unit2 holding[0..2]:", client.read_holding(2, 0, 3))
    server.shutdown()
    server.server_close()
    time.sleep(0.05)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Local Modbus device simulator")
    sub = parser.add_subparsers(dest="mode", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default="examples/devices.json")
    common.add_argument("--host", default="127.0.0.1")
    common.add_argument("--port", type=int, default=0)

    p_tcp = sub.add_parser("tcp", parents=[common])
    p_tcp.set_defaults(func=cmd_tcp)

    p_rtu = sub.add_parser("rtu", parents=[common])
    p_rtu.add_argument("--crc-error-response", action="store_true",
                       help="reply to bad-CRC frames instead of silently dropping")
    p_rtu.set_defaults(func=cmd_rtu)

    p_demo = sub.add_parser("demo")
    p_demo.set_defaults(func=cmd_demo)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
