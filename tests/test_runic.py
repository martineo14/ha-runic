"""Tests for the Runic.io integration."""

import base64
import hashlib
import hmac
import json
import time

from homeassistant import config_entries
from homeassistant.const import CONF_WEBHOOK_ID
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.runic.const import (
    API_BASE,
    CONF_API_TOKEN,
    CONF_SIGNING_SECRET,
    DOMAIN,
    EVENT_RUNIC,
)
from custom_components.runic.coordinator import is_moving
from custom_components.runic.signature import signature_problem, verify_signature

SECRET = "whsec_" + base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()
VEHICLES = {
    "vehicles": [
        {
            "id": 58806,
            "name": "Van 12",
            "latitude": 45.0,
            "longitude": 12.0,
            "timestamp": 1785879671,
            "status": "RUN",
        }
    ]
}


def details(speed: float, age: int = 30) -> dict:
    """GET /vehicles/{id}: a parked tracker still reports RUN while it sends heartbeats."""
    return {
        "vehicle": {
            **VEHICLES["vehicles"][0],
            "plate": "AB123CD",
            "speed": speed,
            "heading": 117,
            "timestamp": int(time.time()) - age,
        }
    }
ALERT = {
    "id": "evt_44133984",
    "type": "alert.movement",
    "created_at": "2026-09-28T15:21:21Z",
    "api_version": "2026-10-01",
    "company": {"id": 16665, "name": "Acme"},
    "alert_rule_id": 84449,
    "level": "info",
    "vehicle": {"id": 58806, "name": "Van 12", "plate": "AB123CD", "device_id": 40371},
    "position": {"lat": 45.057208, "lon": 12.056632, "at": "2026-09-28T15:21:05Z"},
    "data": {},
    "message": "Vehicle is running",
}


def sign(body: bytes, secret: str = SECRET, msg_id: str = "evt_44133984", ts: int | None = None):
    ts = ts or int(time.time())
    key = base64.b64decode(secret.removeprefix("whsec_"))
    sig = base64.b64encode(
        hmac.new(key, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()
    ).decode()
    return {"webhook-id": msg_id, "webhook-timestamp": str(ts), "webhook-signature": f"v1,{sig}"}


def test_signature():
    body = b'{"a":1}'
    assert verify_signature(SECRET, sign(body), body)
    assert not verify_signature(SECRET, sign(body), b'{"a":2}')
    assert not verify_signature(SECRET, sign(body, ts=int(time.time()) - 600), body)
    assert "clock" in signature_problem(SECRET, sign(body, ts=int(time.time()) - 600), body)
    assert "doesn't match" in signature_problem(SECRET, sign(body), b'{"a":2}')
    # Rotation: old + new signatures, either one may match.
    other = "whsec_" + base64.b64encode(b"x" * 32).decode()
    headers = sign(body)
    headers["webhook-signature"] = f"{sign(body, other)['webhook-signature']} {headers['webhook-signature']}"
    assert verify_signature(SECRET, headers, body)


STATUS = {
    "vehicle_status": [
        {"measure_id": 36, "type": "STR", "value": "03.28.07", "timestamp": 1785912937},
        {"measure_id": 39, "type": "FLOAT", "value": 12.6, "timestamp": 1785912937},
        {"measure_id": 41, "type": "FLOAT", "value": 79.1, "timestamp": 1785912937},
        {"measure_id": 62, "type": "INT", "value": 0, "timestamp": 1785912937},
        {"measure_id": 112, "type": "MEAS_COUNT", "value": False, "timestamp": 1785912937},
    ],
    "odometer": {"km": 4978, "timestamp": 1785912936, "source": "vehicle"},
}


def mock_extras(aioclient_mock, status=STATUS):
    aioclient_mock.get(f"{API_BASE}/vehicles/58806/status", json=status)
    aioclient_mock.get(f"{API_BASE}/vehicles/58806/reports", json={"paths": []})
    aioclient_mock.get(f"{API_BASE}/vehicles/58806/tracks", json={"tracks": [], "stops": []})


def test_is_moving():
    now = time.time()
    assert is_moving({"speed": 0, "timestamp": now, "status": "RUN"}, now) is False
    assert is_moving({"speed": 4, "timestamp": now}, now) is False
    assert is_moving({"speed": 42, "timestamp": now}, now) is True
    assert is_moving({"speed": 42, "timestamp": now - 3600}, now) is False
    assert is_moving({"status": "RUN"}, now) is None


async def test_config_flow(hass: HomeAssistant, aioclient_mock):
    aioclient_mock.get(f"{API_BASE}/vehicles", status=401)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: "bad"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API_BASE}/vehicles", json=VEHICLES)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: "good"}
    )
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "webhook"}
    )
    assert result["step_id"] == "webhook"
    assert "/api/webhook/" in result["description_placeholders"]["webhook_url"]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SIGNING_SECRET: SECRET}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_API_TOKEN] == "good"


async def test_webhook_fires_event_and_moves_tracker(hass: HomeAssistant, aioclient_mock, hass_client_no_auth):
    aioclient_mock.get(f"{API_BASE}/vehicles", json=VEHICLES)
    aioclient_mock.get(f"{API_BASE}/vehicles/58806", json=details(speed=0))
    mock_extras(aioclient_mock)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_API_TOKEN: "t", CONF_WEBHOOK_ID: "hook123", CONF_SIGNING_SECRET: SECRET},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Parked, sending heartbeats: Runic says RUN, but the speed is 0.
    tracker = hass.states.get("device_tracker.van_12")
    assert tracker.attributes["latitude"] == 45.0
    moving = hass.states.get("binary_sensor.van_12_moving")
    assert moving.state == "off"
    assert moving.attributes["runic_status"] == "RUN"
    assert hass.states.get("sensor.van_12_speed").state == "0"

    events = async_capture_events(hass, EVENT_RUNIC)
    client = await hass_client_no_auth()
    body = json.dumps(ALERT).encode()

    resp = await client.post("/api/webhook/hook123", data=body, headers={"webhook-id": "x"})
    assert resp.status == 401
    resp = await client.post("/api/webhook/hook123", data=b"[]", headers=sign(b"[]"))
    assert resp.status == 400

    # The movement alert alone doesn't mean moving: the refresh it triggers reads the speed.
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API_BASE}/vehicles", json=VEHICLES)
    aioclient_mock.get(f"{API_BASE}/vehicles/58806", json=details(speed=38))
    mock_extras(aioclient_mock)
    for _ in range(2):  # second delivery is a retry and must be dropped
        resp = await client.post("/api/webhook/hook123", data=body, headers=sign(body))
        assert resp.status == 200
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["type"] == "alert.movement"
    assert events[0].data["plate"] == "AB123CD"
    assert hass.states.get("binary_sensor.van_12_moving").state == "on"
    assert hass.states.get("sensor.van_12_speed").state == "38"


async def test_device_triggers_and_alert_entity(hass: HomeAssistant, aioclient_mock, hass_client_no_auth):
    from homeassistant.components import automation
    from homeassistant.components.device_automation import DeviceAutomationType
    from homeassistant.helpers import device_registry as dr
    from homeassistant.setup import async_setup_component
    from pytest_homeassistant_custom_component.common import async_get_device_automations

    from custom_components.runic.const import TRIGGER_TYPES

    aioclient_mock.get(f"{API_BASE}/vehicles", json=VEHICLES)
    aioclient_mock.get(f"{API_BASE}/vehicles/58806", json=details(speed=0))
    mock_extras(aioclient_mock)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_API_TOKEN: "t", CONF_WEBHOOK_ID: "hook123", CONF_SIGNING_SECRET: SECRET},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, "58806")})
    triggers = await async_get_device_automations(hass, DeviceAutomationType.TRIGGER, device.id)
    assert sorted(t["type"] for t in triggers if t["domain"] == DOMAIN) == sorted(TRIGGER_TYPES)

    calls = []
    hass.services.async_register("test", "record", lambda call: calls.append(call.data))
    assert await async_setup_component(
        hass,
        automation.DOMAIN,
        {
            automation.DOMAIN: [
                {
                    "triggers": [{"trigger": "device", "domain": DOMAIN, "device_id": device.id, "type": t}],
                    "actions": [{"action": "test.record", "data": {"type": t}}],
                }
                for t in ("poi_enter", "poi_exit", "theft")
            ]
        },
    )

    client = await hass_client_no_auth()
    alert = {
        **ALERT,
        "id": "evt_poi",
        "type": "alert.poi_area",
        "data": {"poi_id": 55, "direction": "in"},
        "message": "La velocit&agrave; rilevata &egrave; 103.0 km/h",
    }
    body = json.dumps(alert).encode()
    resp = await client.post("/api/webhook/hook123", data=body, headers=sign(body, msg_id="evt_poi"))
    assert resp.status == 200
    await hass.async_block_till_done()

    assert [c["type"] for c in calls] == ["poi_enter"]
    state = hass.states.get("event.van_12_last_alert")
    assert state.attributes["event_type"] == "poi_area"
    assert state.attributes["details"] == {"poi_id": 55, "direction": "in"}
    assert state.attributes["message"] == "La velocità rilevata è 103.0 km/h"


async def test_vehicle_page_sensors(hass: HomeAssistant, aioclient_mock, hass_client_no_auth):
    """The Runic vehicle page, as Home Assistant sensors."""
    from homeassistant.helpers import device_registry as dr
    from homeassistant.util import dt as dt_util

    today = dt_util.now().date().isoformat()
    aioclient_mock.get(f"{API_BASE}/vehicles", json=VEHICLES)
    aioclient_mock.get(f"{API_BASE}/vehicles/58806", json=details(speed=0))
    aioclient_mock.get(f"{API_BASE}/vehicles/58806/status", json=STATUS)
    aioclient_mock.get(
        f"{API_BASE}/vehicles/58806/reports",
        json={"paths": [{"vehicle_id": "58806", "date": today, "distance": "6.04", "driving_time": "900", "idle_time": "30"}]},
    )
    start = int(time.time()) - 7200
    aioclient_mock.get(
        f"{API_BASE}/vehicles/58806/tracks",
        json={
            "tracks": [{"from_time": start}, {"from_time": start + 3600}],
            "stops": [{"from_time": start + 600, "to_time": start + 600 + 49 * 60}],
        },
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_API_TOKEN: "t", CONF_WEBHOOK_ID: "hook123", CONF_SIGNING_SECRET: SECRET},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = lambda key: hass.states.get(f"sensor.van_12_{key}").state  # noqa: E731
    assert state("status") == "parked"
    assert state("odometer") == "4978"
    assert state("engine_hours") == "79.1"
    assert state("battery_voltage") == "12.6"
    assert state("distance_today") == "6.04"
    assert state("driving_time_today") == "15.0"
    assert state("trips_today") == "2"
    assert state("stopped_time_today") == "49.0"
    assert hass.states.get("sensor.van_12_last_seen").state.startswith("20")
    assert hass.states.get("sensor.van_12_fuel_level") is None   # no CAN fuel on this vehicle: no empty sensor
    assert state("next_service") == "unknown"

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, "58806")})
    assert device.serial_number == "AB123CD"
    assert device.sw_version == "03.28.07"
    assert device.configuration_url == "https://portal.runic.io"

    # Runic's maintenance alert fills in the next service date.
    client = await hass_client_no_auth()
    body = json.dumps({**ALERT, "id": "evt_m", "type": "alert.maintenance", "data": {"task": "Oil change", "days": 7}}).encode()
    resp = await client.post("/api/webhook/hook123", data=body, headers=sign(body, msg_id="evt_m"))
    assert resp.status == 200
    await hass.async_block_till_done()
    service = hass.states.get("sensor.van_12_next_service")
    assert service.state == (dt_util.now().date() + __import__("datetime").timedelta(days=7)).isoformat()
    assert service.attributes["task"] == "Oil change"
