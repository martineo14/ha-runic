"""The odometer reading at which each vehicle's next service is due, typed in by the user.

Runic's API has no maintenance tasks to read, so this is entered once from the Runic portal's
deadlines; "Km to service" then counts down from the odometer.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.const import EntityCategory, UnitOfLength
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import RunicConfigEntry
from .entity import RunicVehicleEntity, async_track_vehicles


async def async_setup_entry(
    hass: HomeAssistant, entry: RunicConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_track_vehicles(
        entry, coordinator, async_add_entities, lambda vid, vehicle: [RunicServiceAt(coordinator, vid)]
    )


class RunicServiceAt(RunicVehicleEntity, RestoreNumber):
    """Odometer km at which the next service is due."""

    _attr_translation_key = "service_at"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = 2_000_000
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfLength.KILOMETERS

    def __init__(self, coordinator, vehicle_id: int) -> None:
        super().__init__(coordinator, vehicle_id, "service_at")
        self._attr_native_value: float | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_number_data()) and last.native_value is not None:
            self._set(last.native_value)

    async def async_set_native_value(self, value: float) -> None:
        self._set(value)
        self.async_write_ha_state()

    def _set(self, value: float | None) -> None:
        self._attr_native_value = value
        targets: dict[int, Any] = self.coordinator.service_targets
        if value:
            targets[self.vehicle_id] = value
        else:
            targets.pop(self.vehicle_id, None)  # 0 clears it
        self.coordinator.async_update_listeners()  # "Km to service" follows at once
