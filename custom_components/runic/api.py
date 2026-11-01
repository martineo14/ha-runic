"""Minimal client for the Runic Vehicle Monitoring API."""

from __future__ import annotations

from typing import Any

import aiohttp

from .const import API_BASE


class RunicError(Exception):
    """Base error talking to Runic."""


class RunicAuthError(RunicError):
    """The token is missing, invalid or expired."""


class RunicApiClient:
    """Talks to https://api.runic.io/v1 with a Bearer token."""

    def __init__(self, session: aiohttp.ClientSession, token: str) -> None:
        self._session = session
        self._headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    async def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        try:
            async with self._session.get(
                f"{API_BASE}{path}",
                params=params,
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status in (401, 403):
                    raise RunicAuthError(f"Runic refused the token ({resp.status})")
                if resp.status == 429:
                    raise RunicError(
                        f"Rate limited, retry after {resp.headers.get('Retry-After', '?')}s"
                    )
                if resp.status >= 400:
                    raise RunicError(
                        f"HTTP {resp.status} (request id {resp.headers.get('X-Request-Id')})"
                    )
                return await resp.json()
        except aiohttp.ClientError as err:
            raise RunicError(str(err)) from err

    async def list_vehicles(self) -> list[dict[str, Any]]:
        """GET /vehicles: id, name, latitude, longitude, timestamp, status."""
        return (await self._get("/vehicles")).get("vehicles", [])

    async def get_vehicle(self, vehicle_id: int) -> dict[str, Any]:
        """GET /vehicles/{id}: adds plate, heading, altitude, speed."""
        return (await self._get(f"/vehicles/{vehicle_id}"))["vehicle"]

    async def get_status(self, vehicle_id: int) -> dict[str, Any]:
        """GET /vehicles/{id}/status: the vehicle's current measures and total km."""
        return await self._get(f"/vehicles/{vehicle_id}/status")

    async def get_day_report(self, vehicle_id: int, day: str) -> dict[str, Any] | None:
        """GET /vehicles/{id}/reports for one day: distance, driving and idle time."""
        paths = (await self._get(f"/vehicles/{vehicle_id}/reports", {"from_date": day, "to_date": day})).get("paths") or []
        return next((p for p in paths if str(p.get("date")) == day), None)

    async def get_day_tracks(self, vehicle_id: int, day: str) -> dict[str, Any]:
        """GET /vehicles/{id}/tracks for one day: the trips and stops."""
        return await self._get(f"/vehicles/{vehicle_id}/tracks", {"date": day})
