"""Moving / stopped state of each Runic vehicle."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import RunicConfigEntry
from .entity import RunicVehicleEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: RunicConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(RunicMoving(coordinator, vid) for vid in coordinator.data)


class RunicMoving(RunicVehicleEntity, BinarySensorEntity):
    """On while Runic reports status RUN."""

    _attr_device_class = BinarySensorDeviceClass.MOVING

    def __init__(self, coordinator, vehicle_id: int) -> None:
        super().__init__(coordinator, vehicle_id, "moving")

    @property
    def is_on(self) -> bool | None:
        status = self.vehicle.get("status")
        return None if status is None else status == "RUN"
