"""Client for Runic's real-time alert stream (outbound WebSocket, no public URL needed)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import json
import logging
import random
import time
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_WEBHOOK_ID
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .ledger import AlertLedger
from .const import (
    CONF_STREAM,
    DOMAIN,
    SIGNAL_STREAM_STATE,
    STREAM_BACKOFF_MAX,
    STREAM_HELLO_TIMEOUT,
    STREAM_PING_INTERVAL,
    STREAM_STABLE_AFTER,
    STREAM_URL,
)

_LOGGER = logging.getLogger(__name__)

# Close codes from Runic's "Alert stream" docs.
CLOSE_TOKEN_REVOKED = 4401
CLOSE_BAD_MESSAGE = 4400


def uses_stream(entry: ConfigEntry) -> bool:
    """New entries use the stream; entries made before it existed keep their webhook until switched."""
    return entry.options.get(CONF_STREAM, CONF_WEBHOOK_ID not in entry.data)


class StreamAuthError(Exception):
    """Runic refused the token: stop and ask the user for a new one."""


class RunicStream:
    """Keeps the stream connected, resumes after the last processed seq, and hands alerts on."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        session: aiohttp.ClientSession,
        token: str,
        ledger: AlertLedger,
        on_alert: Callable[[dict[str, Any], int], None],
        on_gap: Callable[[], Awaitable[None]],
    ) -> None:
        self._hass = hass
        self._entry_id = entry_id
        self._session = session
        self._token = token
        self._on_alert = on_alert
        self._on_gap = on_gap
        self._ledger = ledger
        self.connected = False

    async def run(self) -> None:
        """Run until cancelled, or until Runic rejects the token (raises StreamAuthError)."""
        delay = 1.0
        while True:
            wait: float | None = None
            started = time.monotonic()
            try:
                await self._connect_once()
            except aiohttp.WSServerHandshakeError as err:
                if err.status in (401, 403):
                    raise StreamAuthError(f"Runic refused the token ({err.status})") from err
                if err.status in (429, 503):
                    wait = _retry_after(err.headers)
                    _LOGGER.warning(
                        "Runic stream answered %s%s",
                        err.status,
                        " (more than 5 connections for this token)" if err.status == 429 else "",
                    )
                else:
                    _LOGGER.debug("Runic stream handshake failed: %s", err)
            except (aiohttp.ClientError, TimeoutError, ValueError) as err:
                _LOGGER.debug("Runic stream connection lost: %s", err)
            finally:
                self._set_connected(False)
            if time.monotonic() - started > STREAM_STABLE_AFTER:
                delay = 1.0  # it stayed up: a later drop starts the backoff from 1 s again
            sleep = wait if wait is not None else delay + random.uniform(0, delay / 2)
            _LOGGER.debug("Reconnecting to the Runic stream in %.0f s", sleep)
            await asyncio.sleep(sleep)
            delay = min(delay * 2, STREAM_BACKOFF_MAX)

    async def _connect_once(self) -> None:
        headers = {"Authorization": f"Bearer {self._token}"}
        # autoping answers WebSocket-level pings; Runic's keep-alive is the text "ping" below.
        async with self._session.ws_connect(STREAM_URL, headers=headers, autoping=True) as ws:
            hello = json.loads(
                (await asyncio.wait_for(ws.receive_str(), STREAM_HELLO_TIMEOUT))
            )
            if hello.get("type") != "hello":
                raise ValueError(f"expected hello, got {hello.get('type')!r}")
            after = self._resume_point(hello)
            await ws.send_str(json.dumps({"type": "resume", "after": after}))
            self._set_connected(True)
            _LOGGER.debug("Runic stream connected, resuming after seq %s", after)

            pinger = asyncio.create_task(self._ping(ws))
            try:
                async for msg in ws:
                    if msg.type is not aiohttp.WSMsgType.TEXT:
                        break
                    if msg.data == "pong":
                        continue
                    await self._handle(json.loads(msg.data))
            finally:
                pinger.cancel()

            if ws.close_code == CLOSE_TOKEN_REVOKED:
                raise StreamAuthError("Runic closed the stream: the token was revoked or expired")
            if ws.close_code == CLOSE_BAD_MESSAGE:
                _LOGGER.error("Runic closed the stream because of an unexpected message (4400)")
            else:
                _LOGGER.debug("Runic stream closed with code %s", ws.close_code)

    def _resume_point(self, hello: dict[str, Any]) -> int:
        last_seq = hello["last_seq"]
        stored = self._ledger.last_seq
        if stored is None:
            return last_seq  # first connect: skip alerts from before the integration existed
        if stored > last_seq:
            # Runic's counter is behind what we stored: a reset on their side. Follow it, or every
            # new alert would look like a replay.
            _LOGGER.warning("Runic stream seq went back (%s → %s); resuming from now", stored, last_seq)
            self._ledger.reset_seq(last_seq)
            return last_seq
        return stored

    async def _handle(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == "alert":
            seq = int(message["seq"])
            alert = message.get("alert")
            if self._ledger.already_processed(seq):
                _LOGGER.debug("Skipping replayed Runic alert seq %s", seq)
            elif isinstance(alert, dict) and "id" in alert and "type" in alert:
                self._on_alert(alert, seq)
            else:
                self._ledger.record(None, seq)  # unreadable: don't ask for it again
        elif kind == "gap":
            _LOGGER.info("Missed Runic alerts older than the stream keeps; refreshing vehicles")
            await self._on_gap()

    async def _ping(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        while True:
            await asyncio.sleep(STREAM_PING_INTERVAL)
            await ws.send_str("ping")

    @callback
    def _set_connected(self, connected: bool) -> None:
        if connected != self.connected:
            self.connected = connected
            async_dispatcher_send(self._hass, SIGNAL_STREAM_STATE.format(self._entry_id), connected)


def _retry_after(headers: Any) -> float:
    try:
        return max(1.0, float((headers or {}).get("Retry-After", 60)))
    except (TypeError, ValueError):
        return 60.0
