"""A bus of simulated devices keyed by unit id, with strict isolation."""

from __future__ import annotations

from .exceptions import GatewayTargetNoResponseError
from .registers import Device, DeviceConfig


class DeviceBus:
    """Holds one :class:`Device` per unit id.

    A request for an unknown unit id never falls through to another unit:
    it raises so the server layer can answer with exception 0x0B (TCP) or
    drop the frame (RTU per spec).
    """

    def __init__(self, devices=None):
        self._devices: dict[int, Device] = {}
        for device in devices or ():
            self.add(device)

    def add(self, device: Device) -> None:
        if device.unit_id in self._devices:
            raise ValueError(f"duplicate unit id {device.unit_id}")
        self._devices[device.unit_id] = device

    def add_config(self, config: DeviceConfig) -> Device:
        device = Device(config)
        self.add(device)
        return device

    def get(self, unit_id: int) -> Device:
        device = self._devices.get(unit_id)
        if device is None:
            raise GatewayTargetNoResponseError(f"unknown unit id {unit_id}")
        return device

    def has(self, unit_id: int) -> bool:
        return unit_id in self._devices

    def unit_ids(self) -> list[int]:
        return sorted(self._devices)
