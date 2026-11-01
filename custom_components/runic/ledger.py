"""Which Runic alerts were already handled, kept on disk so repeats don't fire again after a restart."""

from __future__ import annotations

from collections import deque
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import DOMAIN

KEEP_IDS = 500  # well over a day of alerts for a small fleet; Runic replays at most 72 h, ordered by seq


class AlertLedger:
    """Alert ids already processed (stream and webhook alike) and the last stream seq."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._hass = hass
        self._entry_id = entry_id
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.alerts_{entry_id}")
        self._ids: deque[str] = deque(maxlen=KEEP_IDS)
        self._known: set[str] = set()
        self.last_seq: int | None = None

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if data is None:
            # Versions 0.5–0.7 kept only the seq, in the stream's own file: carry it over.
            old = await Store(self._hass, 1, f"{DOMAIN}.stream_{self._entry_id}").async_load()
            data = {"seq": (old or {}).get("seq"), "ids": []}
        self.last_seq = data.get("seq")
        for alert_id in data.get("ids") or []:
            self._remember(alert_id)

    def seen(self, alert_id: str) -> bool:
        return alert_id in self._known

    def already_processed(self, seq: int) -> bool:
        """Runic sends seq in order, so anything at or below the last one is a replay."""
        return self.last_seq is not None and seq <= self.last_seq

    @callback
    def record(self, alert_id: str | None, seq: int | None = None) -> None:
        """Note an alert as handled and write it to disk right away (a crash then repeats at most this one)."""
        if alert_id is not None:
            self._remember(alert_id)
        if seq is not None and (self.last_seq is None or seq > self.last_seq):
            self.last_seq = seq
        self._store.async_delay_save(self._data, 0)

    @callback
    def reset_seq(self, seq: int) -> None:
        self.last_seq = seq
        self._store.async_delay_save(self._data, 0)

    async def async_save_now(self) -> None:
        await self._store.async_save(self._data())

    def _remember(self, alert_id: str) -> None:
        if alert_id in self._known:
            return
        if len(self._ids) == self._ids.maxlen:
            self._known.discard(self._ids[0])
        self._ids.append(alert_id)
        self._known.add(alert_id)

    def _data(self) -> dict[str, Any]:
        return {"seq": self.last_seq, "ids": list(self._ids)}
