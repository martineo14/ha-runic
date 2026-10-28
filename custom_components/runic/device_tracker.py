"""Location of each Runic vehicle."""

from __future__ import annotations

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import RunicConfigEntry
from .entity import RunicVehicleEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: RunicConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(RunicTracker(coordinator, vid) for vid in coordinator.data)


class RunicTracker(RunicVehicleEntity, TrackerEntity):
    """GPS position from Runic."""

    _attr_name = None
    _attr_source_type = SourceType.GPS

    def __init__(self, coordinator, vehicle_id: int) -> None:
        super().__init__(coordinator, vehicle_id, "location")

    @property
    def latitude(self) -> float | None:
        return self.vehicle.get("latitude")

    @property
    def longitude(self) -> float | None:
        return self.vehicle.get("longitude")
