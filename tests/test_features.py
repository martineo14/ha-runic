"""Named POIs, extra tracker measures and zone sync."""

import json

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.runic.const import (
    API_BASE,
    CONF_API_TOKEN,
    CONF_SIGNING_SECRET,
    DOMAIN,
    EVENT_RUNIC,
    ZONE_POI_ADDRESS,
)
from custom_components.runic.zones import ZoneSync
from homeassistant.const import CONF_WEBHOOK_ID

from .test_runic import ALERT, POIS, SECRET, STATUS, VEHICLES, details, mock_extras, sign

RICH_STATUS = {
    **STATUS,
    "vehicle_status": STATUS["vehicle_status"] + [
        {"measure_id": 16, "type": "INT", "value": 1, "timestamp": 1},
        {"measure_id": 60, "type": "INT", "value": 0, "timestamp": 1},
        {"measure_id": 61, "type": "INT", "value": 0, "timestamp": 1},
        {"measure_id": 115, "type": "INT", "value": 0x2000 | 0x100, "timestamp": 1},
        {"measure_id": 52, "type": "INT", "value": 1, "timestamp": 1},
        {"measure_id": 20, "type": "FLOAT", "value": 4.5, "timestamp": 1},
    ],
}


async def setup(hass, aioclient_mock, status):
    aioclient_mock.get(f"{API_BASE}/vehicles", json=VEHICLES)
    aioclient_mock.get(f"{API_BASE}/vehicles/58806", json=details(speed=0))
    mock_extras(aioclient_mock, status=status)
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_API_TOKEN: "t", CONF_WEBHOOK_ID: "hook123", CONF_SIGNING_SECRET: SECRET}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_named_pois(hass: HomeAssistant, aioclient_mock, hass_client_no_auth):
    await setup(hass, aioclient_mock, STATUS)
    # Van 12 is at 45.0, 12.0: inside the 300 m Warehouse POI.
    poi = hass.states.get("sensor.van_12_point_of_interest")
    assert poi.state == "Warehouse"
    assert poi.attributes["options"] == ["away", "Office", "Warehouse"]

    events = async_capture_events(hass, EVENT_RUNIC)
    client = await hass_client_no_auth()
    body = json.dumps({**ALERT, "id": "evt_p", "type": "alert.poi_area", "data": {"poi_id": 55, "direction": "in"}}).encode()
    assert (await client.post("/api/webhook/hook123", data=body, headers=sign(body, msg_id="evt_p"))).status == 200
    await hass.async_block_till_done()
    assert events[0].data["poi_name"] == "Warehouse"
    assert hass.states.get("event.van_12_last_alert").attributes["poi_name"] == "Warehouse"


async def test_tracker_measures(hass: HomeAssistant, aioclient_mock):
    await setup(hass, aioclient_mock, RICH_STATUS)
    assert hass.states.get("binary_sensor.van_12_ignition").state == "on"
    assert hass.states.get("binary_sensor.van_12_engine").state == "off"
    assert hass.states.get("binary_sensor.van_12_immobilizer").state == "off"
    doors = hass.states.get("binary_sensor.van_12_doors")
    assert doors.state == "on"
    assert doors.attributes["trunk"] is True and doors.attributes["front_left"] is True
    assert doors.attributes["rear_right"] is False
    assert hass.states.get("sensor.van_12_temperature").state == "4.5"
    # Digital inputs are created but off by default: they're often not wired.
    entry = er.async_get(hass).async_get("binary_sensor.van_12_digital_input_1")
    assert entry is not None and entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    # Measures the vehicle doesn't report get no entity.
    assert hass.states.get("sensor.van_12_humidity") is None


class FakeRunicAccount:
    """POIs and alert rules, as the Runic API would keep them."""

    def __init__(self, pois, rules):
        self.pois = {p["poi_id"]: dict(p) for p in pois}
        self.rules = {r["alert_id"]: dict(r) for r in rules}
        self.next_id = 900
        self.calls = []

    async def list_pois(self):
        return list(self.pois.values())

    async def create_poi(self, name, latitude, longitude, radius_m, address):
        self.next_id += 1
        self.pois[self.next_id] = {"poi_id": self.next_id, "name": name, "latitude": latitude,
                                   "longitude": longitude, "radius_m": radius_m, "address": address}
        self.calls.append(("create_poi", name))
        return self.next_id

    async def delete_poi(self, poi_id):
        del self.pois[poi_id]
        self.calls.append(("delete_poi", poi_id))

    async def list_alert_rules(self):
        return list(self.rules.values())

    async def create_alert_rule(self, rule):
        self.next_id += 1
        self.rules[self.next_id] = {"alert_id": self.next_id, **rule}
        self.calls.append(("create_rule", rule["settings"]["poi_id"], rule["settings"]["direction"]))
        return self.next_id

    async def delete_alert_rule(self, alert_id):
        del self.rules[alert_id]
        self.calls.append(("delete_rule", alert_id))


async def test_zone_sync(hass: HomeAssistant):
    hass.states.async_set("zone.home", "0", {"latitude": 45.1, "longitude": 12.1, "radius": 100, "friendly_name": "Home"})
    hass.states.async_set("zone.office", "0", {"latitude": 45.2, "longitude": 12.2, "radius": 150, "friendly_name": "Office"})
    hass.states.async_set("zone.secret", "0", {"latitude": 45.3, "longitude": 12.3, "radius": 30, "friendly_name": "Gym", "passive": True})
    account = FakeRunicAccount(
        pois=[
            # synced earlier and unchanged
            {"poi_id": 1, "name": "Home", "latitude": 45.1, "longitude": 12.1, "radius_m": 100, "address": ZONE_POI_ADDRESS.format("zone.home")},
            # its zone was removed from Home Assistant
            {"poi_id": 2, "name": "Old depot", "latitude": 44.0, "longitude": 11.0, "radius_m": 300, "address": ZONE_POI_ADDRESS.format("zone.old_depot")},
            # drawn in the Runic app: never touched
            {"poi_id": 3, "name": "Customer", "latitude": 45.2, "longitude": 12.2, "radius_m": 150, "address": "Via Roma 1"},
        ],
        rules=[
            {"alert_id": 10, "type": "poi_area", "vehicle_id": 0, "settings": {"poi_id": 1, "direction": "in"}},
            {"alert_id": 11, "type": "poi_area", "vehicle_id": 0, "settings": {"poi_id": 2, "direction": "in"}},
            {"alert_id": 12, "type": "poi_area", "vehicle_id": 0, "settings": {"poi_id": 3, "direction": "in"}},
        ],
    )
    refreshed = []

    async def on_done():
        refreshed.append(True)

    sync = ZoneSync(hass, account, "ops@example.com", on_done)
    counts = await sync.async_sync()

    assert counts == {"created": 1, "replaced": 0, "removed": 1, "rules": 3}
    names = sorted(p["name"] for p in account.pois.values())
    assert names == ["Customer", "Home", "Office"]          # passive Gym skipped, Old depot removed
    assert 3 in account.pois and 12 in account.rules         # the Runic-made POI and its rule untouched
    assert 11 not in account.rules                           # the removed POI's rule went with it
    office = next(p for p in account.pois.values() if p["name"] == "Office")
    assert office["radius_m"] == 150 and office["address"] == ZONE_POI_ADDRESS.format("zone.office")
    assert sorted(c[2] for c in account.calls if c[0] == "create_rule" and c[1] == office["poi_id"]) == ["in", "out"]
    assert ("create_rule", 1, "out") in account.calls        # Home had only its "in" rule
    assert refreshed == [True]

    # Moving a zone replaces its POI; running again with no change does nothing.
    hass.states.async_set("zone.home", "0", {"latitude": 45.15, "longitude": 12.1, "radius": 40, "friendly_name": "Home"})
    counts = await sync.async_sync()
    assert counts["replaced"] == 1
    home = next(p for p in account.pois.values() if p["name"] == "Home")
    assert home["radius_m"] == 50                            # Runic's minimum radius
    assert await sync.async_sync() == {"created": 0, "replaced": 0, "removed": 0, "rules": 0}


async def test_zone_sync_without_email_creates_no_rules(hass: HomeAssistant):
    hass.states.async_set("zone.home", "0", {"latitude": 45.1, "longitude": 12.1, "radius": 100, "friendly_name": "Home"})
    account = FakeRunicAccount(pois=[], rules=[])

    async def on_done():
        pass

    counts = await ZoneSync(hass, account, None, on_done).async_sync()
    assert counts == {"created": 1, "replaced": 0, "removed": 0, "rules": 0}
    assert account.rules == {}


async def test_zone_sync_end_to_end(hass: HomeAssistant, aioclient_mock):
    from custom_components.runic.const import CONF_STREAM, CONF_SYNC_ZONES, CONF_ZONE_ALERT_EMAIL

    hass.states.async_set("zone.depot", "0", {"latitude": 44.0678, "longitude": 12.5695, "radius": 300, "friendly_name": "Depot"})
    aioclient_mock.get(f"{API_BASE}/vehicles", json=VEHICLES)
    aioclient_mock.get(f"{API_BASE}/vehicles/58806", json=details(speed=0))
    mock_extras(aioclient_mock)
    aioclient_mock.get(f"{API_BASE}/alerts", json={"result": "SUCCESS", "alerts": []})
    aioclient_mock.post(f"{API_BASE}/pois", json={"result": "SUCCESS", "poi_id": 77})
    aioclient_mock.post(f"{API_BASE}/alerts", json={"result": "SUCCESS", "alert_id": 5})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_API_TOKEN: "t", CONF_WEBHOOK_ID: "hook123", CONF_SIGNING_SECRET: SECRET},
        options={CONF_STREAM: False, CONF_SYNC_ZONES: True, CONF_ZONE_ALERT_EMAIL: "ops@example.com"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    posts = [(str(url), body) for method, url, body, _ in aioclient_mock.mock_calls if method.upper() == "POST"]
    # The test instance also has its default zone.home, which is synced the same way.
    assert (
        f"{API_BASE}/pois",
        {"name": "Depot", "latitude": 44.0678, "longitude": 12.5695, "radius_m": 300, "address": "Home Assistant zone zone.depot"},
    ) == posts[0]
    rules = [body for url, body in posts[1:3]]
    assert all(url.endswith("/alerts") for url, _ in posts[1:3])
    assert [r["settings"] for r in rules] == [{"poi_id": 77, "direction": "in"}, {"poi_id": 77, "direction": "out"}]
    assert rules[0]["notify"] == {"emails": ["ops@example.com"]} and rules[0]["vehicle_id"] == 0

    # The action runs a sync on demand.
    calls_before = len(aioclient_mock.mock_calls)
    await hass.services.async_call(DOMAIN, "sync_zones", blocking=True)
    assert len(aioclient_mock.mock_calls) > calls_before


async def test_zone_sync_with_list_shaped_rule_settings(hass: HomeAssistant):
    """Runic can list a rule's settings as a list; the sync must still run (v0.7.0 crashed here)."""
    hass.states.async_set("zone.home", "0", {"latitude": 45.1, "longitude": 12.1, "radius": 100, "friendly_name": "Home"})
    account = FakeRunicAccount(
        pois=[{"poi_id": 3, "name": "Customer", "latitude": 45.2, "longitude": 12.2, "radius_m": 150, "address": "Via Roma 1"}],
        rules=[
            {"alert_id": 12, "type": "poi_area", "vehicle_id": 0,
             "settings": [{"key": "poi_id", "value": "3"}, {"key": "direction", "value": "in"}]},
            {"alert_id": 13, "type": "speed", "vehicle_id": 0, "settings": [{"key": "limit", "value": "90"}]},
        ],
    )

    async def on_done():
        return None

    counts = await ZoneSync(hass, account, None, on_done).async_sync()
    assert counts["created"] == 1
    assert ("create_poi", "Home") in account.calls
    assert 12 in account.rules  # the app-made POI's rule is untouched

    account.calls.clear()
    counts = await ZoneSync(hass, account, None, on_done).async_sync()
    assert not any(counts.values()) and account.calls == []


def test_summarize_tracks_accepts_lists():
    from custom_components.runic.coordinator import summarize_tracks

    assert summarize_tracks({"tracks": [{}, {}], "stops": [{"from_time": 0, "to_time": 60}]}) == (2, 60)
    assert summarize_tracks([]) == (0, 0)
    assert summarize_tracks([{"tracks": [{}, {}, {}], "stops": []}]) == (3, 0)
    assert summarize_tracks(
        [{"from_time": 0, "to_time": 10}, {"type": "stop", "from_time": 10, "to_time": 70}, {"from_time": 70}]
    ) == (2, 60)


async def test_vehicle_added_later_appears_without_reload(hass: HomeAssistant, aioclient_mock):
    from homeassistant.helpers import device_registry as dr

    from .test_runic import STATUS

    await setup(hass, aioclient_mock, STATUS)
    assert hass.states.get("device_tracker.truck_3") is None
    assert hass.states.get("binary_sensor.van_12_engine") is None

    # A tracker is added to the Runic account, and Van 12's tracker starts reporting the engine.
    truck = {"id": 70001, "name": "Truck 3", "latitude": 44.0, "longitude": 11.0, "timestamp": 1785879671, "status": "STOP"}
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API_BASE}/vehicles", json={"vehicles": [*VEHICLES["vehicles"], truck]})
    aioclient_mock.get(f"{API_BASE}/vehicles/58806", json=details(speed=0))
    aioclient_mock.get(f"{API_BASE}/vehicles/70001", json={"vehicle": {**truck, "plate": "XY987ZT", "speed": 0}})
    with_engine = {**STATUS, "vehicle_status": STATUS["vehicle_status"] + [{"measure_id": 60, "type": "INT", "value": 1, "timestamp": 1}]}
    mock_extras(aioclient_mock, status=with_engine)
    aioclient_mock.get(f"{API_BASE}/vehicles/70001/status", json={"vehicle_status": [], "odometer": None})
    aioclient_mock.get(f"{API_BASE}/vehicles/70001/reports", json={"paths": []})
    aioclient_mock.get(f"{API_BASE}/vehicles/70001/tracks", json={"tracks": [], "stops": []})

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    await entry.runtime_data.coordinator.async_refresh()   # the next 5-minute update
    await hass.async_block_till_done()

    assert hass.states.get("device_tracker.truck_3") is not None
    assert hass.states.get("sensor.truck_3_status").state == "parked"
    assert hass.states.get("event.truck_3_last_alert") is not None
    assert hass.states.get("sensor.truck_3_trips_today").state == "0"   # its "today" figures fetched straight away
    assert hass.states.get("binary_sensor.van_12_engine").state == "on"
    assert dr.async_get(hass).async_get_device(identifiers={(DOMAIN, "70001")}).serial_number == "XY987ZT"
    # No duplicates of Van 12's existing entities.
    assert len(hass.states.async_entity_ids("device_tracker")) == 2
