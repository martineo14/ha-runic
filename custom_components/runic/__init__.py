"""The Runic.io integration: vehicles from the REST API, alerts from signed webhooks."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import json
import logging

from aiohttp import web

from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_WEBHOOK_ID, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import RunicApiClient
from .const import CONF_API_TOKEN, CONF_SIGNING_SECRET, DOMAIN, EVENT_RUNIC
from .coordinator import RunicCoordinator
from .signature import signature_problem

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.BINARY_SENSOR, Platform.DEVICE_TRACKER, Platform.EVENT, Platform.SENSOR]


@dataclass
class RunicData:
    coordinator: RunicCoordinator
    # Delivery is at-least-once; remember recent ids to drop retries.
    seen_ids: deque[str] = field(default_factory=lambda: deque(maxlen=500))


type RunicConfigEntry = ConfigEntry[RunicData]


async def async_setup_entry(hass: HomeAssistant, entry: RunicConfigEntry) -> bool:
    client = RunicApiClient(async_get_clientsession(hass), entry.data[CONF_API_TOKEN])
    coordinator = RunicCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = RunicData(coordinator)

    webhook.async_register(
        hass,
        DOMAIN,
        entry.title,
        entry.data[CONF_WEBHOOK_ID],
        _make_handler(entry),
        allowed_methods=["POST"],
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: RunicConfigEntry) -> bool:
    webhook.async_unregister(hass, entry.data[CONF_WEBHOOK_ID])
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def _make_handler(entry: RunicConfigEntry):
    async def handle_webhook(
        hass: HomeAssistant, webhook_id: str, request: web.Request
    ) -> web.Response:
        raw = await request.read()
        if problem := signature_problem(entry.data[CONF_SIGNING_SECRET], request.headers, raw):
            _LOGGER.warning("Rejected a Runic webhook (answered 401): %s", problem)
            # 401 is a permanent refusal for Runic after 3 attempts.
            return web.Response(status=401)

        try:
            alert = json.loads(raw)
        except ValueError:
            alert = None
        if not (isinstance(alert, dict) and "id" in alert and "type" in alert):
            # Signed but unreadable: a 4xx stops Runic retrying for a day.
            _LOGGER.warning("Ignored a Runic webhook without a readable alert")
            return web.Response(status=400)
        _LOGGER.debug("Runic alert: %s", alert)

        data = entry.runtime_data
        if alert["id"] in data.seen_ids:
            return web.Response(status=200)
        data.seen_ids.append(alert["id"])

        vehicle = alert.get("vehicle") or {}
        device = (
            dr.async_get(hass).async_get_device(identifiers={(DOMAIN, str(vehicle["id"]))})
            if vehicle.get("id") is not None
            else None
        )
        hass.bus.async_fire(
            EVENT_RUNIC,
            {
                "id": alert["id"],
                "type": alert["type"],  # e.g. alert.movement, alert.poi_area
                "alert": alert["type"].removeprefix("alert."),
                "device_id": device.id if device else None,
                "direction": (alert.get("data") or {}).get("direction"),
                "level": alert.get("level"),
                "created_at": alert.get("created_at"),
                "vehicle_id": vehicle.get("id"),
                "vehicle_name": vehicle.get("name"),
                "plate": vehicle.get("plate"),
                "position": alert.get("position"),
                "data": alert.get("data", {}),
                "message": alert.get("message"),
            },
        )
        data.coordinator.apply_alert(alert)
        return web.Response(status=200)

    return handle_webhook
