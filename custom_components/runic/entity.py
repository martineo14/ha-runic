"""Base entity for Runic vehicles."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, PORTAL_URL, SIGNAL_VEHICLE_REMOVED
from .coordinator import RunicCoordinator
from .devices import device_details


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
    added: dict[int, set[str]] = {}

    @callback
    def _add_new() -> None:
        new = []
        for vid, vehicle in (coordinator.data or {}).items():
            known = added.setdefault(vid, set())
            for entity in entities_for(vid, vehicle):
                if entity.unique_id not in known:
                    known.add(entity.unique_id)
                    new.append(entity)
        if new:
            async_add_entities(new)

    @callback
    def _forget(vid: int) -> None:
        added.pop(vid, None)  # if Runic lists the vehicle again, it gets fresh entities

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))
    entry.async_on_unload(
        async_dispatcher_connect(coordinator.hass, SIGNAL_VEHICLE_REMOVED.format(entry.entry_id), _forget)
    )


class RunicVehicleEntity(CoordinatorEntity[RunicCoordinator]):
    """One entity attached to one vehicle device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: RunicCoordinator, vehicle_id: int, key: str) -> None:
        super().__init__(coordinator)
        self.vehicle_id = vehicle_id
        self._attr_unique_id = f"{vehicle_id}_{key}"
        # Later changes in Runic reach the device through DeviceSync.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(vehicle_id))},
            manufacturer="Runic.io",
            configuration_url=PORTAL_URL,
            **device_details(self.vehicle),
        )

    @property
    def vehicle(self) -> dict[str, Any]:
        return self.coordinator.data.get(self.vehicle_id, {})

    @property
    def available(self) -> bool:
        return super().available and self.vehicle_id in self.coordinator.data
