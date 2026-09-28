"""JSON configuration loading for the device simulator.

Example::

    {
      "devices": [
        {
          "unit_id": 1,
          "holding_size": 100,
          "input_size": 100,
          "word_order": "ABCD",
          "holding": {"0": 1, "2": [10, 20]},
          "inputs":  {"0": 1234}
        }
      ]
    }

All values are plain JSON; files stay on the local disk.
"""

from __future__ import annotations

import json

from .bus import DeviceBus
from .registers import DeviceConfig


def _coerce_mapping(raw) -> dict[int, int]:
    """Accept {"0": 1} and {"2": [10, 20]} as address -> value(s)."""
    out: dict[int, int] = {}
    for key, value in dict(raw or {}).items():
        address = int(key)
        if isinstance(value, (list, tuple)):
            for offset, item in enumerate(value):
                out[address + offset] = int(item)
        else:
            out[address] = int(value)
    return out


def load_config(path: str) -> list[DeviceConfig]:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    configs = []
    for entry in data.get("devices", []):
        configs.append(
            DeviceConfig(
                unit_id=int(entry["unit_id"]),
                holding_size=int(entry.get("holding_size", 100)),
                input_size=int(entry.get("input_size", 100)),
                word_order=entry.get("word_order", "ABCD"),
                holding=_coerce_mapping(entry.get("holding")),
                inputs=_coerce_mapping(entry.get("inputs")),
            )
        )
    if not configs:
        raise ValueError("config defines no devices")
    return configs


def build_bus(configs) -> DeviceBus:
    bus = DeviceBus()
    for config in configs:
        bus.add_config(config)
    return bus
