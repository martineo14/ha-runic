"""Speed of each Runic vehicle."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfSpeed
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import RunicConfigEntry
from .entity import RunicVehicleEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: RunicConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(RunicSpeed(coordinator, vid) for vid in coordinator.data)


class RunicSpeed(RunicVehicleEntity, SensorEntity):
    """GPS speed from the last fix."""

    _attr_translation_key = "speed"
    _attr_device_class = SensorDeviceClass.SPEED
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfSpeed.KILOMETERS_PER_HOUR

    def __init__(self, coordinator, vehicle_id: int) -> None:
        super().__init__(coordinator, vehicle_id, "speed")

    @property
    def native_value(self) -> float | None:
        return self.vehicle.get("speed")
