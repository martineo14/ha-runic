"""Base entity for Runic vehicles."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MEASURE_FIRMWARE, PORTAL_URL
from .coordinator import RunicCoordinator


class RunicVehicleEntity(CoordinatorEntity[RunicCoordinator]):
    """One entity attached to one vehicle device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: RunicCoordinator, vehicle_id: int, key: str) -> None:
        super().__init__(coordinator)
        self.vehicle_id = vehicle_id
        self._attr_unique_id = f"{vehicle_id}_{key}"
        vehicle = self.vehicle
        firmware = (vehicle.get("measures") or {}).get(MEASURE_FIRMWARE)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(vehicle_id))},
            name=vehicle["name"],
            manufacturer="Runic.io",
            model=vehicle.get("description") or None,
            serial_number=vehicle.get("plate") or None,  # shown on the device page, like Runic's plate badge
            sw_version=str(firmware) if firmware else None,
            configuration_url=PORTAL_URL,
        )

    @property
    def vehicle(self) -> dict[str, Any]:
        return self.coordinator.data.get(self.vehicle_id, {})

    @property
    def available(self) -> bool:
        return super().available and self.vehicle_id in self.coordinator.data
