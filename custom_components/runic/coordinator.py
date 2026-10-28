"""Keeps the latest state of every Runic vehicle."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import RunicApiClient, RunicAuthError, RunicError
from .const import DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


class RunicCoordinator(DataUpdateCoordinator[dict[int, dict[str, Any]]]):
    """Polls GET /vehicles; webhook alerts patch positions in between."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: RunicApiClient) -> None:
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=SCAN_INTERVAL
        )
        self.client = client

    async def _async_update_data(self) -> dict[int, dict[str, Any]]:
        try:
            vehicles = await self.client.list_vehicles()
        except RunicAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except RunicError as err:
            raise UpdateFailed(str(err)) from err
        return {v["id"]: v for v in vehicles}

    def apply_alert(self, alert: dict[str, Any]) -> None:
        """Push the position carried by a webhook alert to the entities."""
        vehicle, position = alert.get("vehicle"), alert.get("position")
        if not (vehicle and position and self.data and vehicle["id"] in self.data):
            return
        current = self.data[vehicle["id"]]
        updated = {**current, "latitude": position["lat"], "longitude": position["lon"]}
        if alert["type"] == "alert.movement":
            updated["status"] = "RUN"
        self.async_set_updated_data({**self.data, vehicle["id"]: updated})
