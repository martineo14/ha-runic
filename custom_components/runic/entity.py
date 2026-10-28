"""Base entity for Runic vehicles."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import RunicCoordinator


class RunicVehicleEntity(CoordinatorEntity[RunicCoordinator]):
    """One entity attached to one vehicle device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: RunicCoordinator, vehicle_id: int, key: str) -> None:
        super().__init__(coordinator)
        self.vehicle_id = vehicle_id
        self._attr_unique_id = f"{vehicle_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(vehicle_id))},
            name=self.vehicle["name"],
            manufacturer="Runic.io",
        )

    @property
    def vehicle(self) -> dict[str, Any]:
        return self.coordinator.data.get(self.vehicle_id, {})

    @property
    def available(self) -> bool:
        return super().available and self.vehicle_id in self.coordinator.data
