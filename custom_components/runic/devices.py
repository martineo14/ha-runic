"""Keep each vehicle's device in step with Runic: renamed, re-plated, re-flashed or removed."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import DOMAIN, MEASURE_FIRMWARE, SIGNAL_VEHICLE_REMOVED

_LOGGER = logging.getLogger(__name__)

# A vehicle must be missing from this many successful updates in a row (5 minutes apart)
# before its device is removed, so a one-off odd answer from Runic never deletes anything.
MISSING_UPDATES_BEFORE_REMOVAL = 2


def device_details(vehicle: dict[str, Any]) -> dict[str, str | None]:
    """The device fields that come from Runic and can change there."""
    firmware = (vehicle.get("measures") or {}).get(MEASURE_FIRMWARE)
    return {
        "name": vehicle.get("name"),
        "model": vehicle.get("description") or None,
        "serial_number": vehicle.get("plate") or None,  # shown on the device page, like Runic's plate badge
        "sw_version": str(firmware) if firmware else None,
    }


def vehicle_id_of(device: dr.DeviceEntry) -> str | None:
    """The Runic vehicle id behind a device, or None for the account device."""
    for domain, identifier in device.identifiers:
        if domain == DOMAIN and not identifier.startswith("account_"):
            return identifier
    return None


class DeviceSync:
    """After each successful update: copy changes to the devices, and remove vehicles Runic dropped."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator) -> None:
        self._hass = hass
        self._entry = entry
        self._coordinator = coordinator
        self._missing: dict[str, int] = {}

    @callback
    def async_update(self) -> None:
        if not self._coordinator.last_update_success or self._coordinator.data is None:
            return  # a failed update says nothing about which vehicles exist
        registry = dr.async_get(self._hass)
        vehicles = {str(vid): vehicle for vid, vehicle in self._coordinator.data.items()}

        for device in dr.async_entries_for_config_entry(registry, self._entry.entry_id):
            vid = vehicle_id_of(device)
            if vid is None:
                continue
            if vid in vehicles:
                self._missing.pop(vid, None)
                details = device_details(vehicles[vid])
                # The user's own name (name_by_user) is a separate field and always wins on screen.
                changes = {k: v for k, v in details.items() if v is not None or k != "name"}
                changes = {k: v for k, v in changes.items() if getattr(device, k) != v}
                if changes:
                    _LOGGER.debug("Vehicle %s changed in Runic: %s", vid, changes)
                    registry.async_update_device(device.id, **changes)
                continue

            self._missing[vid] = self._missing.get(vid, 0) + 1
            if self._missing[vid] < MISSING_UPDATES_BEFORE_REMOVAL:
                continue
            _LOGGER.info("Vehicle %s (%s) is no longer in Runic; removing its device", vid, device.name)
            self._missing.pop(vid)
            registry.async_update_device(device.id, remove_config_entry_id=self._entry.entry_id)
            async_dispatcher_send(self._hass, SIGNAL_VEHICLE_REMOVED.format(self._entry.entry_id), int(vid))
