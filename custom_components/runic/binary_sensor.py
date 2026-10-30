"""Moving / stopped state of each Runic vehicle."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import RunicConfigEntry
from .coordinator import is_moving
from .entity import RunicVehicleEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: RunicConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(RunicMoving(coordinator, vid) for vid in coordinator.data)


class RunicMoving(RunicVehicleEntity, BinarySensorEntity):
    """On while the last GPS fix is recent and above walking speed."""

    _attr_device_class = BinarySensorDeviceClass.MOVING

    def __init__(self, coordinator, vehicle_id: int) -> None:
        super().__init__(coordinator, vehicle_id, "moving")

    @property
    def is_on(self) -> bool | None:
        return is_moving(self.vehicle)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        timestamp = self.vehicle.get("timestamp")
        return {
            "speed_kmh": self.vehicle.get("speed"),
            "last_fix": dt_util.utc_from_timestamp(timestamp).isoformat() if timestamp else None,
            # Runic's own status, kept for reference: RUN only means the device is active.
            "runic_status": self.vehicle.get("status"),
        }
