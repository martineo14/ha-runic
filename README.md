🇮🇹 [Leggi in italiano](README.it.md)

# Runic.io for Home Assistant

Custom integration for [Runic.io](https://www.runic.io) GPS trackers.

- **Vehicles** are read from the Runic REST API (`https://api.runic.io/v1`, Bearer token) every 5 minutes,
  and right after each alert. Per vehicle you get:
  - `device_tracker.<vehicle>`: position on the map.
  - `sensor.<vehicle>_speed`: GPS speed of the last fix, in km/h.
  - `binary_sensor.<vehicle>_moving`: on when the last fix is under 15 minutes old and at 5 km/h or more.
    Runic's own `RUN`/`STOP` status is kept as the `runic_status` attribute only: it reads `RUN` whenever
    the tracker is active (heartbeats, ignition), even when the vehicle is parked.
- **Alerts** arrive within seconds over Runic's **real-time stream**: Home Assistant opens an outgoing
  WebSocket, so no public URL, port forwarding or Nabu Casa is needed. Each alert becomes a device trigger
  and a `runic_event`. Signed webhooks are still supported, alone or alongside the stream.
  After a disconnect the stream resumes where it left off (Runic keeps 72 hours), and repeats are dropped.

## Install

### HACS (recommended)

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=martineo14&repository=ha-runic&category=integration)

Or in HACS → ⋮ → **Custom repositories**, add `https://github.com/martineo14/ha-runic` as an *Integration*,
then download **Runic.io** and restart Home Assistant.

### Manual

Copy `custom_components/runic` into your HA `config/custom_components/` and restart.

### Set up

1. In the [Runic portal](https://developers.runic.io/portal/webhooks): **Add endpoint** → **Real-time stream**,
   and tick the alert types you want. One stream per company; there is no URL or secret to copy.
2. In Home Assistant: Settings → Devices & Services → Add Integration → **Runic.io**.
3. Paste your API token (issued by Runic Support, runic@runic.io), then choose **Real-time stream**.
4. Test it: press **Send test event** on the stream endpoint in the portal. Developer Tools → Events,
   listening to `runic_event`, shows an `alert.test`.

The **Alert stream** sensor on the *Runic.io* device shows whether the stream is connected.
If Runic revokes or expires the token, Home Assistant asks you for a new one.

**Webhook instead:** choose **Webhook** in step 3, add the URL Home Assistant shows as a webhook endpoint
in the portal, and paste the `whsec_…` signing secret back. Home Assistant must then be reachable from the
internet over HTTPS (Nabu Casa, Cloudflare Tunnel, reverse proxy), and the URL must not redirect.
An integration set up with a webhook before 0.5.0 keeps it: turn on the stream under
**Runic.io → Configure**.

## Automations

### From the automation editor

Each vehicle has a trigger for every Runic alert type. Add a trigger → **By target** → pick the vehicle
(for example *Truck 3*) → choose the alert: *Theft alarm*, *Started moving*, *Entered a point of interest*,
*Left a point of interest*, *Speed limit exceeded*, *Low vehicle battery*… Or use **Device** → the vehicle → the alert.

Each vehicle also has an `event.<vehicle>_last_alert` entity that shows its latest alert in the dashboard,
history and logbook.

An alert type only fires when two things are set up in Runic: the type is ticked on your stream or webhook
endpoint (developers.runic.io → Webhooks), and an alert rule for it exists in the Runic app.

### In YAML

```yaml
triggers:
  - trigger: event
    event_type: runic_event
    event_data:
      type: alert.poi_area
actions:
  - action: notify.mobile_app_phone
    data:
      message: >
        {{ trigger.event.data.vehicle_name }}
        {{ 'arrived at' if trigger.event.data.data.direction == 'in' else 'left' }}
        POI {{ trigger.event.data.data.poi_id }}
```

Event data: `id`, `type`, `alert` (type without `alert.`), `device_id`, `direction` (geofences), `level`, `created_at`, `vehicle_id`, `vehicle_name`, `plate`,
`position` (`lat`, `lon`, `at`), `data` (type-specific), `message`.

Alert types: `alert.movement`, `alert.poi_area`, `alert.speed_limit`, `alert.theft`, `alert.tow`,
`alert.parking_moved`, `alert.panic_button`, `alert.accident`, `alert.jamming`, `alert.cable_cut`,
`alert.tamper`, `alert.input_activation`, `alert.device_alarm`, `alert.low_battery`,
`alert.low_battery_internal`, `alert.offline`, `alert.fuel`, `alert.fuel_tank`, `alert.maintenance`,
`alert.test`. An alert only fires if an alert rule exists for it in the Runic app.

## Development

```bash
python -m venv .venv && .venv/bin/pip install pytest-homeassistant-custom-component
.venv/bin/pytest
```
