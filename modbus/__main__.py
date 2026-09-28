"""Run the local Modbus TCP simulator from the command line.

Examples::

    python -m modbus                       # units 1..3 on 127.0.0.1:5020
    python -m modbus --config devices.json --port 1502
"""

from __future__ import annotations

import argparse

from .device import DeviceMap, ModbusDevice, WordOrder, load_devices
from .tcp import TCPServer


def demo_device_map() -> DeviceMap:
    """Build an in-memory demo map: three units with isolated spaces."""
    device_map = DeviceMap()

    unit1 = ModbusDevice(1, size=1000, word_order=WordOrder.HIGH_WORD_FIRST)
    unit1.write_multiple(0, [101, 202, 303])
    unit1.write_u32(10, 0x12345678)

    unit2 = ModbusDevice(2, size=1000, word_order=WordOrder.LOW_WORD_FIRST)
    unit2.write_multiple(0, [404, 505, 606])
    unit2.write_u32(10, 0x12345678)  # physically [0x5678, 0x1234]

    unit3 = ModbusDevice(3, size=100)
    unit3.write_multiple(0, [707, 808])

    device_map.add(unit1)
    device_map.add(unit2)
    device_map.add(unit3)
    return device_map


def main() -> None:
    parser = argparse.ArgumentParser(description="Local Modbus TCP device simulator")
    parser.add_argument("--host", default="127.0.0.1", help="bind host (loopback only by default)")
    parser.add_argument("--port", type=int, default=5020, help="TCP port")
    parser.add_argument("--config", help="path to a local devices.json file")
    args = parser.parse_args()

    device_map = load_devices(args.config) if args.config else demo_device_map()
    server = TCPServer(device_map, host=args.host, port=args.port)
    print(f"Modbus TCP simulator on {args.host}:{server.port}")
    print(f"units: {device_map.unit_ids()}")
    print("Ctrl-C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
