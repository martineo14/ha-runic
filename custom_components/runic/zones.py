"""Mirror Home Assistant zones as Runic points of interest, so Runic raises enter and leave alerts for them."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.const import ATTR_FRIENDLY_NAME, ATTR_LATITUDE, ATTR_LONGITUDE, EVENT_STATE_CHANGED
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.debounce import Debouncer

from .api import RunicApiClient, RunicError
from .const import ZONE_POI_ADDRESS, ZONE_SYNC_DELAY

_LOGGER = logging.getLogger(__name__)

RADIUS_MIN, RADIUS_MAX = 50, 50_000  # what Runic accepts for a POI


def desired_pois(hass: HomeAssistant) -> dict[str, dict[str, Any]]:
    """The POI each active Home Assistant zone should have, by zone entity id."""
    wanted = {}
    for state in hass.states.async_all("zone"):
        attrs = state.attributes
        if attrs.get("passive") or attrs.get(ATTR_LATITUDE) is None:
            continue  # passive zones are only for automations, not presence
        wanted[state.entity_id] = {
            "name": attrs.get(ATTR_FRIENDLY_NAME) or state.object_id,
            "latitude": round(float(attrs[ATTR_LATITUDE]), 6),
            "longitude": round(float(attrs[ATTR_LONGITUDE]), 6),
            "radius_m": min(RADIUS_MAX, max(RADIUS_MIN, round(float(attrs.get("radius", 100))))),
            "address": ZONE_POI_ADDRESS.format(state.entity_id),
        }
    return wanted


def rule_settings(rule: dict[str, Any]) -> dict[str, Any]:
    """An alert rule's settings as a dict.

    Runic documents them as an object, but the live API can return a list: either of objects to merge,
    or of {key|name, value} pairs. Anything else counts as no settings, so one odd rule never stops a sync.
    """
    raw = rule.get("settings")
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, list):
        return {}
    merged: dict[str, Any] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = item.get("key", item.get("name"))
        if key is not None and "value" in item:
            merged[str(key)] = item["value"]
        else:
            merged.update(item)
    if not merged and raw:
        _LOGGER.debug("Unrecognised settings on alert rule %s: %r", rule.get("alert_id"), raw)
    return merged


def _rule_poi_id(rule: dict[str, Any]) -> int | None:
    try:
        return int(rule_settings(rule)["poi_id"])
    except (KeyError, TypeError, ValueError):
        return None


def _same(poi: dict[str, Any], want: dict[str, Any]) -> bool:
    return (
        poi.get("name") == want["name"]
        and round(float(poi["latitude"]), 6) == want["latitude"]
        and round(float(poi["longitude"]), 6) == want["longitude"]
        and int(poi["radius_m"]) == want["radius_m"]
    )


class ZoneSync:
    """Creates, replaces and removes the POIs it owns; never touches POIs made in the Runic app."""

    def __init__(self, hass: HomeAssistant, client: RunicApiClient, alert_email: str | None, on_done) -> None:
        self._hass = hass
        self._client = client
        self._email = alert_email or None
        self._on_done = on_done
        self._debouncer = Debouncer(
            hass, _LOGGER, cooldown=ZONE_SYNC_DELAY, immediate=False, function=self.async_sync
        )

    @callback
    def async_listen(self) -> CALLBACK_TYPE:
        """Sync again whenever a zone is added, moved, resized, renamed or removed."""

        @callback
        def _changed(event: Event) -> None:
            if event.data["entity_id"].startswith("zone."):
                self._hass.async_create_task(self._debouncer.async_call())

        unsub = self._hass.bus.async_listen(EVENT_STATE_CHANGED, _changed)

        def _stop() -> None:
            unsub()
            self._debouncer.async_cancel()

        return _stop

    async def async_sync(self) -> dict[str, int]:
        """Bring Runic's POIs in line with the zones. Returns counts of what changed."""
        counts = {"created": 0, "replaced": 0, "removed": 0, "rules": 0}
        try:
            wanted = desired_pois(self._hass)
            ours = {
                p["address"].removeprefix(ZONE_POI_ADDRESS.format("")): p
                for p in await self._client.list_pois()
                if str(p.get("address", "")).startswith(ZONE_POI_ADDRESS.format(""))
            }
            rules = await self._client.list_alert_rules()

            for zone_id, poi in ours.items():
                if zone_id not in wanted or not _same(poi, wanted[zone_id]):
                    await self._remove(poi, rules)
                    counts["removed" if zone_id not in wanted else "replaced"] += 1

            for zone_id, want in wanted.items():
                poi = ours.get(zone_id)
                if poi is None or not _same(poi, want):
                    poi_id = await self._client.create_poi(**want)
                    counts["created"] += poi is None
                    if poi is not None:  # the replaced POI's rules were deleted with it
                        rules = [r for r in rules if _rule_poi_id(r) != int(poi["poi_id"])]
                else:
                    poi_id = int(poi["poi_id"])
                counts["rules"] += await self._ensure_rules(poi_id, rules)
        except RunicError as err:
            _LOGGER.warning("Could not sync Home Assistant zones to Runic: %s", err)
            return counts

        if any(counts.values()):
            _LOGGER.info("Synced zones to Runic POIs: %s", counts)
            await self._on_done()
        return counts

    async def _remove(self, poi: dict[str, Any], rules: list[dict[str, Any]]) -> None:
        poi_id = int(poi["poi_id"])
        for rule in rules:
            if rule.get("type") == "poi_area" and _rule_poi_id(rule) == poi_id:
                await self._client.delete_alert_rule(int(rule["alert_id"]))
        await self._client.delete_poi(poi_id)

    async def _ensure_rules(self, poi_id: int, rules: list[dict[str, Any]]) -> int:
        """An enter and a leave rule for every vehicle, when an email for Runic's notifications is set."""
        if not self._email:
            return 0
        existing = {
            rule_settings(rule).get("direction")
            for rule in rules
            if rule.get("type") == "poi_area" and _rule_poi_id(rule) == poi_id
        }
        made = 0
        for direction in ("in", "out"):
            if direction not in existing:
                await self._client.create_alert_rule(
                    {
                        "type": "poi_area",
                        "vehicle_id": 0,  # every vehicle of the account
                        "settings": {"poi_id": poi_id, "direction": direction},
                        "notify": {"emails": [self._email]},
                    }
                )
                made += 1
        return made
