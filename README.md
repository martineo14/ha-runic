# Runic.io for Home Assistant

Custom integration for [Runic.io](https://www.runic.io) GPS trackers.

- **Vehicles** are read from the Runic REST API (`https://api.runic.io/v1`, Bearer token) every 5 minutes,
  and right after each alert. Per vehicle you get:
  - `device_tracker.<vehicle>`: position on the map.
  - `sensor.<vehicle>_speed`: GPS speed of the last fix, in km/h.
  - `binary_sensor.<vehicle>_moving`: on when the last fix is under 15 minutes old and at 5 km/h or more.
    Runic's own `RUN`/`STOP` status is kept as the `runic_status` attribute only: it reads `RUN` whenever
    the tracker is active (heartbeats, ignition), even when the vehicle is parked.
- **Alerts** arrive instantly as signed Runic webhooks and are fired on the HA event bus as `runic_event`.
  Signatures (Standard Webhooks, HMAC-SHA256), timestamps and duplicate deliveries are checked.

## Install

### HACS (recommended)

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=martineo14&repository=ha-runic&category=integration)

Or in HACS → ⋮ → **Custom repositories**, add `https://github.com/martineo14/ha-runic` as an *Integration*,
then download **Runic.io** and restart Home Assistant.

### Manual

Copy `custom_components/runic` into your HA `config/custom_components/` and restart.

### Set up

1. Settings → Devices & Services → Add Integration → **Runic.io**.
2. Paste your API token (issued by Runic Support, runic@runic.io).
3. Add the shown webhook URL in the [Runic portal](https://developers.runic.io/portal/webhooks),
   pick alert types, and paste the `whsec_…` signing secret back into HA.

HA must be reachable from the internet over HTTPS (Nabu Casa, Cloudflare Tunnel, reverse proxy),
and the URL must not redirect: Runic does not follow redirects.

## Automations

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

Event data: `id`, `type`, `level`, `created_at`, `vehicle_id`, `vehicle_name`, `plate`,
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
