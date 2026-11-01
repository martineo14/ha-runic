"""Tests for the real-time alert stream, against a fake Runic server on localhost."""

import asyncio
import json
import time

from aiohttp import WSCloseCode, web
import pytest

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.runic import stream as stream_module
from custom_components.runic import api as api_module
from custom_components.runic.const import CONF_API_TOKEN, CONF_STREAM, DOMAIN, EVENT_RUNIC

VEHICLE = {"id": 58806, "name": "Van 12", "latitude": 45.0, "longitude": 12.0, "timestamp": 0, "status": "STOP"}


def alert(alert_id: str, kind: str = "alert.theft") -> dict:
    return {
        "id": alert_id,
        "type": kind,
        "created_at": "2026-11-01T03:07:00Z",
        "level": "critical",
        "company": {"id": 16665, "name": "Acme"},
        "vehicle": {"id": 58806, "name": "Van 12", "plate": "AB123CD", "device_id": 40371},
        "position": {"lat": 45.1, "lon": 12.1, "at": "2026-11-01T03:06:58Z"},
        "data": {"alarm_code": 2},
        "message": "Theft alarm",
    }


class FakeRunic:
    """REST endpoints plus /v1/alerts/stream, scripted per connection."""

    def __init__(self) -> None:
        self.scripts: list = []          # one coroutine function per expected connection
        self.resumes: list[int] = []
        self.pings = 0
        self.vehicle_calls = 0
        self.connections = 0
        self.handshake_status: int | None = None

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/v1/vehicles", self._vehicles)
        app.router.add_get("/v1/vehicles/{vid}", self._vehicle)
        app.router.add_get("/v1/vehicles/{vid}/status", self._json({"vehicle_status": [], "odometer": None}))
        app.router.add_get("/v1/vehicles/{vid}/reports", self._json({"paths": []}))
        app.router.add_get("/v1/vehicles/{vid}/tracks", self._json({"tracks": [], "stops": []}))
        app.router.add_get("/v1/pois", self._json({"result": "SUCCESS", "pois": []}))
        app.router.add_get("/v1/alerts/stream", self._stream)
        return app

    @staticmethod
    def _json(body):
        async def handler(request):
            return web.json_response(body)

        return handler

    async def _vehicles(self, request):
        self.vehicle_calls += 1
        return web.json_response({"vehicles": [VEHICLE]})

    async def _vehicle(self, request):
        return web.json_response({"vehicle": {**VEHICLE, "speed": 0, "timestamp": int(time.time())}})

    async def _stream(self, request):
        assert request.headers["Authorization"] == "Bearer good"
        if self.handshake_status:
            return web.Response(status=self.handshake_status)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        script = self.scripts[self.connections] if self.connections < len(self.scripts) else None
        self.connections += 1
        if script:
            await script(self, ws)
        else:
            await self.hello_and_resume(ws, last_seq=0)
            await self.serve(ws, until_closed=True)
        return ws

    async def hello_and_resume(self, ws, last_seq: int) -> int:
        await ws.send_json({"type": "hello", "protocol": 1, "last_seq": last_seq, "oldest_seq": 1, "retention_hours": 72})
        msg = json.loads((await ws.receive()).data)
        assert msg["type"] == "resume"
        self.resumes.append(msg["after"])
        return msg["after"]

    async def serve(self, ws, until_closed=False, seconds=0.0):
        """Answer pings like Runic does, until the client leaves or the time is up."""
        deadline = asyncio.get_running_loop().time() + seconds
        while until_closed or asyncio.get_running_loop().time() < deadline:
            try:
                msg = await asyncio.wait_for(ws.receive(), 0.05)
            except TimeoutError:
                continue
            if msg.type is not web.WSMsgType.TEXT:
                return
            if msg.data == "ping":
                self.pings += 1
                await ws.send_str("pong")


@pytest.fixture
async def runic(hass, monkeypatch, socket_enabled):
    fake = FakeRunic()
    runner = web.AppRunner(fake.app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setattr(api_module, "API_BASE", f"http://127.0.0.1:{port}/v1")
    monkeypatch.setattr(stream_module, "STREAM_URL", f"ws://127.0.0.1:{port}/v1/alerts/stream")
    monkeypatch.setattr(stream_module, "STREAM_PING_INTERVAL", 0.05)
    monkeypatch.setattr(stream_module.random, "uniform", lambda a, b: 0)
    yield fake
    await runner.cleanup()


async def wait_for(condition, timeout=5.0):
    end = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


async def setup_entry(hass) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Runic.io", data={CONF_API_TOKEN: "good"}, options={CONF_STREAM: True}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_stream_alerts_resume_and_reconnect(hass: HomeAssistant, runic: FakeRunic, monkeypatch):
    monkeypatch.setattr(stream_module, "STREAM_STABLE_AFTER", 0)

    async def first(fake, ws):
        await fake.hello_and_resume(ws, last_seq=10)
        await fake.serve(ws, seconds=0.2)                      # a few pings go by
        await ws.send_json({"type": "alert", "seq": 11, "alert": alert("evt_1")})
        await ws.send_json({"type": "alert", "seq": 12, "alert": alert("evt_1")})   # redelivery
        await ws.send_json({"type": "gap", "after": 3, "oldest_seq": 5})
        await fake.serve(ws, seconds=0.2)
        await ws.close(code=WSCloseCode.GOING_AWAY)            # a deploy

    async def second(fake, ws):
        await fake.hello_and_resume(ws, last_seq=12)
        await ws.send_json({"type": "alert", "seq": 13, "alert": alert("evt_2", "alert.movement")})
        await fake.serve(ws, until_closed=True)

    runic.scripts = [first, second]
    events = async_capture_events(hass, EVENT_RUNIC)
    entry = await setup_entry(hass)
    calls_after_setup = runic.vehicle_calls

    await wait_for(lambda: len(events) == 2)
    assert runic.resumes == [10, 12]          # first connect skips old alerts, then resumes after seq 12
    assert [e.data["id"] for e in events] == ["evt_1", "evt_2"]   # the repeat of evt_1 was dropped
    assert events[0].data["source"] == "stream"
    assert events[0].data["alert"] == "theft"
    assert runic.pings >= 2                    # text "ping" keep-alive, answered with "pong"
    assert runic.vehicle_calls > calls_after_setup  # the gap made it refresh vehicles through REST
    assert hass.states.get("binary_sensor.runic_io_alert_stream").state == "on"
    assert hass.states.get("event.van_12_last_alert").attributes["event_type"] == "movement"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_stream_remembers_seq_across_restarts(hass: HomeAssistant, runic: FakeRunic):
    async def first(fake, ws):
        await fake.hello_and_resume(ws, last_seq=40)
        await ws.send_json({"type": "alert", "seq": 41, "alert": alert("evt_41")})
        await fake.serve(ws, until_closed=True)

    async def after_restart(fake, ws):
        await fake.hello_and_resume(ws, last_seq=45)
        await fake.serve(ws, until_closed=True)

    runic.scripts = [first, after_restart]
    events = async_capture_events(hass, EVENT_RUNIC)
    entry = await setup_entry(hass)
    await wait_for(lambda: len(events) == 1)

    assert await hass.config_entries.async_reload(entry.entry_id)
    await wait_for(lambda: len(runic.resumes) == 2)
    assert runic.resumes == [40, 41]           # resumed after the last processed alert, not "now"
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.parametrize("how", ["close_4401", "handshake_401"])
async def test_revoked_token_asks_for_a_new_one(hass: HomeAssistant, runic: FakeRunic, how):
    async def revoke(fake, ws):
        await fake.hello_and_resume(ws, last_seq=1)
        await ws.close(code=4401)

    if how == "close_4401":
        runic.scripts = [revoke]
    else:
        runic.handshake_status = 401
    entry = await setup_entry(hass)

    await wait_for(lambda: any(f["context"]["source"] == "reauth" for f in hass.config_entries.flow.async_progress()))
    await asyncio.sleep(0.2)
    assert runic.connections <= 1              # it did not keep reconnecting with the bad token

    flow = next(f for f in hass.config_entries.flow.async_progress() if f["context"]["source"] == "reauth")
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], {CONF_API_TOKEN: "good"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_config_flow_stream_choice(hass: HomeAssistant, runic: FakeRunic):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_TOKEN: "good"})
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["stream", "webhook"]
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "stream"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_API_TOKEN: "good"}
    assert result["options"] == {CONF_STREAM: True}
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_replayed_alert_after_restart_fires_once(hass: HomeAssistant, runic: FakeRunic):
    """At-least-once delivery: an alert Runic sends again after a restart must not fire twice."""

    async def first(fake, ws):
        await fake.hello_and_resume(ws, last_seq=10)
        await ws.send_json({"type": "alert", "seq": 11, "alert": alert("evt_11")})
        await fake.serve(ws, until_closed=True)

    async def after_restart(fake, ws):
        # Runic replays seq 11 (e.g. the restart happened before the seq was saved), then a new one.
        await fake.hello_and_resume(ws, last_seq=12)
        await ws.send_json({"type": "alert", "seq": 11, "alert": alert("evt_11")})
        await ws.send_json({"type": "alert", "seq": 12, "alert": alert("evt_12")})
        await fake.serve(ws, until_closed=True)

    runic.scripts = [first, after_restart]
    events = async_capture_events(hass, EVENT_RUNIC)
    entry = await setup_entry(hass)
    await wait_for(lambda: len(events) == 1)

    assert await hass.config_entries.async_reload(entry.entry_id)
    await wait_for(lambda: any(e.data["id"] == "evt_12" for e in events))
    assert [e.data["id"] for e in events] == ["evt_11", "evt_12"]
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_webhook_copy_of_a_stream_alert_after_restart_fires_once(
    hass: HomeAssistant, runic: FakeRunic, hass_client_no_auth
):
    """Stream and webhook both on: the same alert arriving through each, across a restart, fires once."""
    from custom_components.runic.const import CONF_SIGNING_SECRET
    from homeassistant.const import CONF_WEBHOOK_ID

    from .test_runic import SECRET, sign

    async def stream_once(fake, ws):
        await fake.hello_and_resume(ws, last_seq=20)
        await ws.send_json({"type": "alert", "seq": 21, "alert": alert("evt_21")})
        await fake.serve(ws, until_closed=True)

    runic.scripts = [stream_once]
    events = async_capture_events(hass, EVENT_RUNIC)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Runic.io",
        data={CONF_API_TOKEN: "good", CONF_WEBHOOK_ID: "hook123", CONF_SIGNING_SECRET: SECRET},
        options={CONF_STREAM: True},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await wait_for(lambda: len(events) == 1)

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_client_no_auth()
    body = json.dumps(alert("evt_21")).encode()
    resp = await client.post("/api/webhook/hook123", data=body, headers=sign(body, msg_id="evt_21"))
    assert resp.status == 200
    await hass.async_block_till_done()
    assert [e.data["id"] for e in events] == ["evt_21"]
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_alert_is_on_disk_before_any_shutdown(hass: HomeAssistant, runic: FakeRunic, hass_storage):
    """A crash right after an alert must not lose the record of it: it's written without waiting."""

    async def one(fake, ws):
        await fake.hello_and_resume(ws, last_seq=30)
        await ws.send_json({"type": "alert", "seq": 31, "alert": alert("evt_31")})
        await fake.serve(ws, until_closed=True)

    runic.scripts = [one]
    events = async_capture_events(hass, EVENT_RUNIC)
    entry = await setup_entry(hass)
    await wait_for(lambda: len(events) == 1)
    key = f"runic.alerts_{entry.entry_id}"
    await wait_for(lambda: "evt_31" in (hass_storage.get(key) or {}).get("data", {}).get("ids", []), timeout=2)
    assert hass_storage[key]["data"]["seq"] == 31
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_counter_reset_on_runic_side_still_delivers(hass: HomeAssistant, runic: FakeRunic, hass_storage):
    entry_id = "reset01"
    hass_storage[f"runic.alerts_{entry_id}"] = {"version": 1, "key": f"runic.alerts_{entry_id}", "data": {"seq": 500, "ids": []}}

    async def after_reset(fake, ws):
        await fake.hello_and_resume(ws, last_seq=4)           # Runic's counter restarted from zero
        await ws.send_json({"type": "alert", "seq": 5, "alert": alert("evt_new")})
        await fake.serve(ws, until_closed=True)

    runic.scripts = [after_reset]
    events = async_capture_events(hass, EVENT_RUNIC)
    entry = MockConfigEntry(
        domain=DOMAIN, entry_id=entry_id, title="Runic.io", data={CONF_API_TOKEN: "good"}, options={CONF_STREAM: True}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await wait_for(lambda: len(events) == 1)
    assert runic.resumes == [4]                               # followed the reset instead of waiting for seq 500
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_seq_saved_by_older_versions_is_kept(hass: HomeAssistant, runic: FakeRunic, hass_storage):
    entry_id = "old01"
    hass_storage[f"runic.stream_{entry_id}"] = {"version": 1, "key": f"runic.stream_{entry_id}", "data": {"seq": 77}}

    async def resume(fake, ws):
        await fake.hello_and_resume(ws, last_seq=80)
        await fake.serve(ws, until_closed=True)

    runic.scripts = [resume]
    entry = MockConfigEntry(
        domain=DOMAIN, entry_id=entry_id, title="Runic.io", data={CONF_API_TOKEN: "good"}, options={CONF_STREAM: True}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await wait_for(lambda: len(runic.resumes) == 1)
    assert runic.resumes == [77]                              # resumed where 0.7.0 stopped, nothing skipped
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
