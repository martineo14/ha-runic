"""Keeps the latest state of every Runic vehicle."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import RunicApiClient, RunicAuthError, RunicError
from .const import DOMAIN, MOVING_MAX_FIX_AGE, MOVING_MIN_SPEED_KMH, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


def is_moving(vehicle: dict[str, Any], now: float | None = None) -> bool | None:
    """Moving means a recent GPS fix above walking speed, not Runic's RUN status."""
    speed, timestamp = vehicle.get("speed"), vehicle.get("timestamp")
    if speed is None or timestamp is None:
        return None
    if (now or time.time()) - timestamp > MOVING_MAX_FIX_AGE.total_seconds():
        return False
    return speed >= MOVING_MIN_SPEED_KMH


class RunicCoordinator(DataUpdateCoordinator[dict[int, dict[str, Any]]]):
    """Polls the vehicles; webhook alerts patch positions and trigger a refresh."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: RunicApiClient) -> None:
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=SCAN_INTERVAL
        )
        self.client = client

    async def _async_update_data(self) -> dict[int, dict[str, Any]]:
        try:
            vehicles = await self.client.list_vehicles()
            # The list has no speed; the details endpoint does.
            details = await asyncio.gather(
                *(self.client.get_vehicle(v["id"]) for v in vehicles), return_exceptions=True
            )
        except RunicAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except RunicError as err:
            raise UpdateFailed(str(err)) from err

        result = {}
        for summary, detail in zip(vehicles, details):
            if isinstance(detail, RunicAuthError):
                raise ConfigEntryAuthFailed(str(detail)) from detail
            if isinstance(detail, BaseException):
                _LOGGER.debug("No details for vehicle %s: %s", summary["id"], detail)
                detail = {}
            result[summary["id"]] = {**summary, **detail}
        return result

    def apply_alert(self, alert: dict[str, Any]) -> None:
        """Move the vehicle to the alert's position, then fetch its real speed."""
        vehicle, position = alert.get("vehicle"), alert.get("position")
        if not (vehicle and self.data and vehicle.get("id") in self.data):
            return
        if position:
            current = self.data[vehicle["id"]]
            updated = {**current, "latitude": position["lat"], "longitude": position["lon"]}
            self.async_set_updated_data({**self.data, vehicle["id"]: updated})
        # Debounced, so a burst of alerts costs one API round.
        self.hass.async_create_task(self.async_request_refresh())
