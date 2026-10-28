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

    async def _get(self, path: str) -> dict[str, Any]:
        try:
            async with self._session.get(
                f"{API_BASE}{path}",
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
