"""Keeps the latest state of every Runic vehicle."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import RunicApiClient, RunicAuthError, RunicError
from .const import (
    DOMAIN,
    MOVING_MAX_FIX_AGE,
    MOVING_MIN_SPEED_KMH,
    SCAN_INTERVAL,
    TODAY_SCAN_INTERVAL,
)

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
        # Runic points of interest by id, for names in alerts and the "Point of interest" sensor.
        self.pois: dict[int, dict[str, Any]] = {}
        # Odometer km of the next service, per vehicle, from the "Service at" number entities.
        self.service_targets: dict[int, float] = {}

    async def _async_update_data(self) -> dict[int, dict[str, Any]]:
        try:
            vehicles = await self.client.list_vehicles()
            # The list has no speed; the details endpoint does. The status adds km, engine hours, battery…
            details = await asyncio.gather(
                *(self.client.get_vehicle(v["id"]) for v in vehicles), return_exceptions=True
            )
            statuses = await asyncio.gather(
                *(self.client.get_status(v["id"]) for v in vehicles), return_exceptions=True
            )
            try:
                self.pois = {int(p["poi_id"]): p for p in await self.client.list_pois()}
            except RunicAuthError:
                raise
            except RunicError as err:
                _LOGGER.debug("Keeping the previous POIs: %s", err)
        except RunicAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except RunicError as err:
            raise UpdateFailed(str(err)) from err

        result = {}
        for summary, detail, status in zip(vehicles, details, statuses):
            for answer in (detail, status):
                if isinstance(answer, RunicAuthError):
                    raise ConfigEntryAuthFailed(str(answer)) from answer
            if isinstance(detail, BaseException):
                _LOGGER.debug("No details for vehicle %s: %s", summary["id"], detail)
                detail = {}
            vehicle = {**summary, **detail}
            if isinstance(status, BaseException):
                _LOGGER.debug("No status for vehicle %s: %s", summary["id"], status)
            else:
                vehicle["measures"] = {
                    m["measure_id"]: m.get("value")
                    for m in status.get("vehicle_status") or []
                    if m.get("value") is not False  # composite measures carry no scalar value
                }
                vehicle["odometer_km"] = (status.get("odometer") or {}).get("km")
            result[summary["id"]] = vehicle
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


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def split_tracks(tracks: dict[str, Any] | list[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The trips and the stops in a /tracks answer (its shape varies, per Runic's docs).

    Usually {"tracks": [...], "stops": [...]}, but the live API can also answer with a bare list:
    either that object wrapped in a list, or the segments themselves (stops marked by a "type").
    "tracks" itself may be a single trip object instead of a list.
    """
    if isinstance(tracks, list):
        if len(tracks) == 1 and isinstance(tracks[0], dict) and ({"tracks", "stops"} & tracks[0].keys()):
            return split_tracks(tracks[0])
        segments = [s for s in tracks if isinstance(s, dict)]
        stops = [s for s in segments if str(s.get("type", "")).lower() in ("stop", "stopped", "parking")]
        return [s for s in segments if s not in stops], stops
    moves = tracks.get("tracks")
    if isinstance(moves, dict):
        moves = [moves] if moves else []
    return [m for m in moves or [] if isinstance(m, dict)], [s for s in tracks.get("stops") or [] if isinstance(s, dict)]


def summarize_tracks(tracks: dict[str, Any] | list[Any]) -> tuple[int, int]:
    """Number of trips and seconds stopped in a /tracks answer."""
    moves, stops = split_tracks(tracks)
    stopped = sum(
        max(0, int(stop["to_time"]) - int(stop["from_time"]))
        for stop in stops
        if stop.get("from_time") is not None and stop.get("to_time") is not None
    )
    return len(moves), stopped


def last_trip(tracks: dict[str, Any] | list[Any]) -> dict[str, Any] | None:
    """The most recent trip: start, end, km and top speed."""
    moves = [m for m in split_tracks(tracks)[0] if m.get("from_time") is not None]
    if not moves:
        return None
    trip = max(moves, key=lambda m: int(m.get("to_time") or m["from_time"]))
    start, end = int(trip["from_time"]), int(trip.get("to_time") or trip["from_time"])
    return {
        "start": start,
        "end": end,
        "distance_km": _number(trip.get("distance")),
        "duration_s": max(0, end - start),
        "max_speed": _number(trip.get("max_speed")),
    }


class RunicTodayCoordinator(DataUpdateCoordinator[dict[int, dict[str, Any]]]):
    """Today's distance, driving time, trips and stops for each vehicle."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: RunicApiClient, vehicles: RunicCoordinator) -> None:
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=f"{DOMAIN}_today", update_interval=TODAY_SCAN_INTERVAL
        )
        self.client = client
        self.vehicles = vehicles
        # The last trip stays known after midnight, until the vehicle drives again.
        self._last_trips: dict[int, dict[str, Any]] = {}

    async def _async_update_data(self) -> dict[int, dict[str, Any]]:
        day = dt_util.now().date().isoformat()  # Runic reads dates in the account's timezone
        ids = list(self.vehicles.data or {})
        reports = await asyncio.gather(*(self.client.get_day_report(v, day) for v in ids), return_exceptions=True)
        tracks = await asyncio.gather(*(self.client.get_day_tracks(v, day) for v in ids), return_exceptions=True)
        result: dict[int, dict[str, Any]] = {}
        for vid, report, track in zip(ids, reports, tracks):
            for answer in (report, track):
                if isinstance(answer, RunicAuthError):
                    raise ConfigEntryAuthFailed(str(answer)) from answer
            today: dict[str, Any] = {"date": day}
            if isinstance(report, BaseException):
                _LOGGER.debug("No report today for vehicle %s: %s", vid, report)
            else:
                report = report or {}  # no report yet: the vehicle hasn't moved today
                today["distance_km"] = _number(report.get("distance")) or 0.0
                today["driving_s"] = int(_number(report.get("driving_time")) or 0)
                today["idle_s"] = int(_number(report.get("idle_time")) or 0)
                today["max_speed"] = _number(report.get("max_speed"))
                today["avg_fuel"] = _number(report.get("avg_fuel"))
            if isinstance(track, BaseException):
                _LOGGER.debug("No tracks today for vehicle %s: %s", vid, track)
            else:
                try:
                    today["trips"], today["stopped_s"] = summarize_tracks(track)
                    if trip := last_trip(track):
                        self._last_trips[vid] = trip
                except (TypeError, ValueError, KeyError, AttributeError) as err:
                    _LOGGER.debug("Unexpected tracks for vehicle %s: %s", vid, err)
            today["last_trip"] = self._last_trips.get(vid)
            result[vid] = today
        if ids and all(isinstance(r, BaseException) for r in (*reports, *tracks)):
            raise UpdateFailed("Runic returned no reports or tracks")
        return result


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(a))


def poi_at(vehicle: dict[str, Any], pois: dict[int, dict[str, Any]]) -> dict[str, Any] | None:
    """The smallest Runic POI the vehicle is inside, if any."""
    lat, lon = vehicle.get("latitude"), vehicle.get("longitude")
    if lat is None or lon is None:
        return None
    inside = [
        p for p in pois.values()
        if distance_m(lat, lon, p["latitude"], p["longitude"]) <= p["radius_m"]
    ]
    return min(inside, key=lambda p: p["radius_m"], default=None)
