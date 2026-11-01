"""The Runic.io integration: vehicles from the REST API, alerts from the real-time stream or signed webhooks."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import html
import json
import logging
from typing import Any

from aiohttp import web

from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_WEBHOOK_ID, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import RunicApiClient
from .const import CONF_API_TOKEN, CONF_SIGNING_SECRET, DOMAIN, EVENT_RUNIC
from .coordinator import RunicCoordinator
from .signature import signature_problem
from .stream import RunicStream, StreamAuthError, uses_stream

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.BINARY_SENSOR, Platform.DEVICE_TRACKER, Platform.EVENT, Platform.SENSOR]


@dataclass
class RunicData:
    coordinator: RunicCoordinator
    stream: RunicStream | None = None
    # Delivery is at-least-once, and the stream and webhooks can both be on: drop repeats by alert id.
    seen_ids: deque[str] = field(default_factory=lambda: deque(maxlen=500))


type RunicConfigEntry = ConfigEntry[RunicData]


async def async_setup_entry(hass: HomeAssistant, entry: RunicConfigEntry) -> bool:
    session = async_get_clientsession(hass)
    token = entry.data[CONF_API_TOKEN]
    coordinator = RunicCoordinator(hass, entry, RunicApiClient(session, token))
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = RunicData(coordinator)

    if CONF_WEBHOOK_ID in entry.data:
        webhook.async_register(
            hass, DOMAIN, entry.title, entry.data[CONF_WEBHOOK_ID], _make_handler(entry), allowed_methods=["POST"]
        )

    if uses_stream(entry):
        entry.runtime_data.stream = RunicStream(
            hass,
            entry.entry_id,
            session,
            token,
            on_alert=lambda alert: async_process_alert(hass, entry, alert, "stream"),
            on_gap=coordinator.async_request_refresh,
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if stream := entry.runtime_data.stream:
        entry.async_create_background_task(hass, _run_stream(hass, entry, stream), "runic_stream")

    entry.async_on_unload(entry.add_update_listener(_options_updated))
    return True


async def _run_stream(hass: HomeAssistant, entry: RunicConfigEntry, stream: RunicStream) -> None:
    try:
        await stream.run()
    except StreamAuthError as err:
        _LOGGER.warning("%s; asking for a new token", err)
        entry.async_start_reauth(hass)


async def _options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: RunicConfigEntry) -> bool:
    if CONF_WEBHOOK_ID in entry.data:
        webhook.async_unregister(hass, entry.data[CONF_WEBHOOK_ID])
    if entry.runtime_data.stream:
        await entry.runtime_data.stream.async_save_now()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


@callback
def async_process_alert(
    hass: HomeAssistant, entry: RunicConfigEntry, alert: dict[str, Any], source: str
) -> None:
    """Handle one Runic alert, whichever way it arrived."""
    _LOGGER.debug("Runic alert from the %s: %s", source, alert)
    data = entry.runtime_data
    if alert["id"] in data.seen_ids:
        return
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
            # Runic sends HTML entities, e.g. "velocit&agrave;".
            "message": html.unescape(alert["message"]) if alert.get("message") else None,
            "source": source,
        },
    )
    data.coordinator.apply_alert(alert)


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

        async_process_alert(hass, entry, alert, "webhook")
        return web.Response(status=200)

    return handle_webhook
