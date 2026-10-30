"""The last Runic alert of each vehicle, kept in history and the logbook."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import RunicConfigEntry
from .const import ALERT_TYPES, EVENT_RUNIC
from .entity import RunicVehicleEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: RunicConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(RunicAlert(coordinator, vid) for vid in coordinator.data)


class RunicAlert(RunicVehicleEntity, EventEntity):
    """Fires with the alert type each time Runic sends an alert for this vehicle."""

    _attr_translation_key = "alert"
    _attr_event_types = ALERT_TYPES

    def __init__(self, coordinator, vehicle_id: int) -> None:
        super().__init__(coordinator, vehicle_id, "alert")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.hass.bus.async_listen(EVENT_RUNIC, self._on_alert))

    @callback
    def _on_alert(self, event: Event) -> None:
        data = event.data
        if data.get("vehicle_id") != self.vehicle_id or data["alert"] not in ALERT_TYPES:
            return
        self._trigger_event(
            data["alert"],
            {
                "level": data.get("level"),
                "message": data.get("message"),
                "created_at": data.get("created_at"),
                "position": data.get("position"),
                "details": data.get("data"),
            },
        )
        self.async_write_ha_state()
