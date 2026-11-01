"""Moving / stopped state of each Runic vehicle."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import RunicConfigEntry
from .const import DOMAIN, SIGNAL_STREAM_STATE
from .coordinator import is_moving
from .entity import RunicVehicleEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: RunicConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(RunicMoving(coordinator, vid) for vid in coordinator.data)
    if entry.runtime_data.stream:
        async_add_entities([RunicStreamConnected(entry)])


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


class RunicStreamConnected(BinarySensorEntity):
    """On while the real-time alert stream is connected."""

    _attr_has_entity_name = True
    _attr_translation_key = "stream"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, entry: RunicConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_stream"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"account_{entry.entry_id}")},
            name=entry.title,
            manufacturer="Runic.io",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def is_on(self) -> bool:
        return self._entry.runtime_data.stream.connected

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_STREAM_STATE.format(self._entry.entry_id), self._on_state
            )
        )

    @callback
    def _on_state(self, connected: bool) -> None:
        self.async_write_ha_state()
