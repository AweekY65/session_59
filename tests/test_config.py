"""Config loading and bus isolation tests."""

import json

import pytest

from modbus_sim.config import build_bus, load_config
from modbus_sim.exceptions import GatewayTargetNoResponseError
from modbus_sim.registers import DeviceConfig, WordOrder


def test_load_example_config():
    configs = load_config("examples/devices.json")
    assert [c.unit_id for c in configs] == [1, 2, 10]
    assert configs[0].word_order == WordOrder.ABCD
    assert configs[1].word_order == WordOrder.CDAB
    bus = build_bus(configs)
    assert bus.unit_ids() == [1, 2, 10]
    assert bus.get(1).read_holding(0, 1) == [1111]
    # list-style seed values expand across consecutive addresses
    assert bus.get(1).read_holding(10, 3) == [100, 200, 300]
    assert bus.get(1).read_input(1, 1) == [9002]


def test_unknown_unit_raises_and_isolates(tmp_path):
    path = tmp_path / "d.json"
    path.write_text(
        json.dumps(
            {
                "devices": [
                    {"unit_id": 1, "holding_size": 2, "input_size": 2},
                    {"unit_id": 2, "holding_size": 2, "input_size": 2},
                ]
            }
        )
    )
    bus = build_bus(load_config(str(path)))
    bus.get(1).write_registers(0, [0xAAAA])
    bus.get(2).write_registers(0, [0x5555])
    with pytest.raises(GatewayTargetNoResponseError):
        bus.get(3)
    assert bus.get(1).read_holding(0, 1) == [0xAAAA]
    assert bus.get(2).read_holding(0, 1) == [0x5555]


def test_duplicate_unit_rejected():
    bus = build_bus([DeviceConfig(1)])
    with pytest.raises(ValueError):
        bus.add_config(DeviceConfig(1))
