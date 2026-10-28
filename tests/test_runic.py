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
from custom_components.runic.signature import verify_signature

SECRET = "whsec_" + base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()
VEHICLES = {
    "vehicles": [
        {
            "id": 58806,
            "name": "Van 12",
            "latitude": 45.0,
            "longitude": 12.0,
            "timestamp": 1785879671,
            "status": "STOP",
        }
    ]
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
    # Rotation: old + new signatures, either one may match.
    other = "whsec_" + base64.b64encode(b"x" * 32).decode()
    headers = sign(body)
    headers["webhook-signature"] = f"{sign(body, other)['webhook-signature']} {headers['webhook-signature']}"
    assert verify_signature(SECRET, headers, body)


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
    assert result["step_id"] == "webhook"
    assert "/api/webhook/" in result["description_placeholders"]["webhook_url"]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SIGNING_SECRET: SECRET}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_API_TOKEN] == "good"


async def test_webhook_fires_event_and_moves_tracker(hass: HomeAssistant, aioclient_mock, hass_client_no_auth):
    aioclient_mock.get(f"{API_BASE}/vehicles", json=VEHICLES)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_API_TOKEN: "t", CONF_WEBHOOK_ID: "hook123", CONF_SIGNING_SECRET: SECRET},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    tracker = hass.states.get("device_tracker.van_12")
    assert tracker.attributes["latitude"] == 45.0
    assert hass.states.get("binary_sensor.van_12_moving").state == "off"

    events = async_capture_events(hass, EVENT_RUNIC)
    client = await hass_client_no_auth()
    body = json.dumps(ALERT).encode()

    resp = await client.post("/api/webhook/hook123", data=body, headers={"webhook-id": "x"})
    assert resp.status == 401

    for _ in range(2):  # second delivery is a retry and must be dropped
        resp = await client.post("/api/webhook/hook123", data=body, headers=sign(body))
        assert resp.status == 200
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data["type"] == "alert.movement"
    assert events[0].data["plate"] == "AB123CD"
    assert hass.states.get("device_tracker.van_12").attributes["latitude"] == 45.057208
    assert hass.states.get("binary_sensor.van_12_moving").state == "on"
