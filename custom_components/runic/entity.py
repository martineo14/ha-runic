"""Base entity for Runic vehicles."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MEASURE_FIRMWARE, PORTAL_URL
from .coordinator import RunicCoordinator


@callback
def async_track_vehicles(
    entry: ConfigEntry,
    coordinator: RunicCoordinator,
    async_add_entities: AddEntitiesCallback,
    entities_for: Callable[[int, dict[str, Any]], Iterable[Entity]],
) -> None:
    """Add entities now, and again after every update for vehicles (or values) Runic reports later.

    A vehicle added to the Runic account, or a tracker that starts sending a new value, gets its
    entities at the next update without reloading the integration.
    """
    added: set[str] = set()

    @callback
    def _add_new() -> None:
        new = [
            entity
            for vid, vehicle in (coordinator.data or {}).items()
            for entity in entities_for(vid, vehicle)
            if entity.unique_id not in added
        ]
        if new:
            added.update(e.unique_id for e in new)
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


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
