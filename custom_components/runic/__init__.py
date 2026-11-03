"""The Runic.io integration: vehicles from the REST API, alerts from the real-time stream or signed webhooks."""

from __future__ import annotations

from dataclasses import dataclass
import html
import json
import logging
from typing import Any

from aiohttp import web

from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_WEBHOOK_ID, Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import RunicApiClient
from .const import (
    CONF_API_TOKEN,
    CONF_SIGNING_SECRET,
    CONF_SYNC_ZONES,
    CONF_ZONE_ALERT_EMAIL,
    DOMAIN,
    EVENT_RUNIC,
)
from .coordinator import RunicCoordinator, RunicTodayCoordinator
from .devices import DeviceSync, vehicle_id_of
from .ledger import AlertLedger
from .signature import signature_problem
from .stream import RunicStream, StreamAuthError, uses_stream
from .zones import ZoneSync

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.DEVICE_TRACKER,
    Platform.EVENT,
    Platform.NUMBER,
    Platform.SENSOR,
]


@dataclass
class RunicData:
    coordinator: RunicCoordinator
    today: RunicTodayCoordinator
    # Alerts already handled, on disk: delivery is at-least-once, and stream and webhook can both be on.
    ledger: AlertLedger
    stream: RunicStream | None = None
    zone_sync: ZoneSync | None = None


type RunicConfigEntry = ConfigEntry[RunicData]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async def sync_zones(call: ServiceCall) -> None:
        syncs = [
            e.runtime_data.zone_sync
            for e in hass.config_entries.async_loaded_entries(DOMAIN)
            if e.runtime_data.zone_sync
        ]
        if not syncs:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="zone_sync_off"
            )
        for sync in syncs:
            await sync.async_sync()

    hass.services.async_register(DOMAIN, "sync_zones", sync_zones)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: RunicConfigEntry) -> bool:
    session = async_get_clientsession(hass)
    token = entry.data[CONF_API_TOKEN]
    client = RunicApiClient(session, token)
    coordinator = RunicCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    today = RunicTodayCoordinator(hass, entry, client, coordinator)
    await today.async_refresh()  # optional extras: a failure here must not block setup
    ledger = AlertLedger(hass, entry.entry_id)
    await ledger.async_load()
    entry.runtime_data = RunicData(coordinator, today, ledger)

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
            ledger,
            on_alert=lambda alert, seq: async_process_alert(hass, entry, alert, "stream", seq),
            on_gap=coordinator.async_request_refresh,
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if stream := entry.runtime_data.stream:
        entry.async_create_background_task(hass, _run_stream(hass, entry, stream), "runic_stream")

    if entry.options.get(CONF_SYNC_ZONES):
        zone_sync = ZoneSync(
            hass, client, entry.options.get(CONF_ZONE_ALERT_EMAIL), on_done=coordinator.async_request_refresh
        )
        entry.runtime_data.zone_sync = zone_sync
        entry.async_on_unload(zone_sync.async_listen())

        async def _sync_when_started(_: HomeAssistant) -> None:
            await zone_sync.async_sync()

        entry.async_on_unload(async_at_started(hass, _sync_when_started))

    device_sync = DeviceSync(hass, entry, coordinator)
    entry.async_on_unload(coordinator.async_add_listener(device_sync.async_update))

    entry.async_on_unload(entry.add_update_listener(_options_updated))
    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: RunicConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow "Delete" on a vehicle's device only once Runic no longer has that vehicle."""
    vid = vehicle_id_of(device)
    if vid is None:
        return False  # the account device belongs to the integration itself
    return int(vid) not in (entry.runtime_data.coordinator.data or {})


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
    await entry.runtime_data.ledger.async_save_now()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


@callback
def async_process_alert(
    hass: HomeAssistant,
    entry: RunicConfigEntry,
    alert: dict[str, Any],
    source: str,
    seq: int | None = None,
) -> None:
    """Handle one Runic alert, whichever way it arrived."""
    _LOGGER.debug("Runic alert from the %s: %s", source, alert)
    data = entry.runtime_data
    if data.ledger.seen(alert["id"]):
        data.ledger.record(None, seq)  # e.g. the webhook copy came first: still move the stream on
        return

    vehicle = alert.get("vehicle") or {}
    details = alert.get("data") or {}
    poi = None
    if details.get("poi_id") is not None:
        try:
            poi = data.coordinator.pois.get(int(details["poi_id"]))
        except (TypeError, ValueError):
            pass
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
            "direction": details.get("direction"),
            "poi_name": poi["name"] if poi else None,
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
    # After handling, so a crash in between repeats the alert rather than losing it.
    data.ledger.record(alert["id"], seq)


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
