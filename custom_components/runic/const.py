"""Constants for the Runic.io integration."""

from datetime import timedelta

DOMAIN = "runic"
EVENT_RUNIC = f"{DOMAIN}_event"

API_BASE = "https://api.runic.io/v1"
STREAM_URL = "wss://api.runic.io/v1/alerts/stream"

CONF_API_TOKEN = "api_token"
CONF_SIGNING_SECRET = "signing_secret"
CONF_STREAM = "stream"
CONF_SYNC_ZONES = "sync_zones"
CONF_ZONE_ALERT_EMAIL = "zone_alert_email"

# POIs this integration creates from Home Assistant zones carry this address, so it never touches others.
ZONE_POI_ADDRESS = "Home Assistant zone {}"
ZONE_SYNC_DELAY = 30  # seconds to wait after a zone change, so a burst of edits syncs once

# Alert stream timings, from Runic's "Alert stream" docs: a connection silent for 2 minutes is closed.
STREAM_PING_INTERVAL = 30
STREAM_HELLO_TIMEOUT = 30
STREAM_BACKOFF_MAX = 300
STREAM_STABLE_AFTER = 60
SIGNAL_STREAM_STATE = f"{DOMAIN}_stream_state_{{}}"

# Today's trips and stops: the tracks are large and change slowly.
TODAY_SCAN_INTERVAL = timedelta(minutes=15)

# Measure ids from the catalogue in Runic's /vehicles/{id}/status docs.
MEASURE_GSM_SIGNAL = 14
MEASURE_IGNITION = 16  # ACC input: 0 low, 1 high
MEASURE_TEMPERATURE = 20
MEASURE_FUEL_USED_CAN = 51
MEASURE_DIGITAL_INPUTS = (52, 53, 54, 55)
MEASURE_ENGINE = 60  # 0 off, 1 on
MEASURE_IMMOBILIZER = 61  # 0 inactive, 1 active
MEASURE_HUMIDITY = 69
MEASURE_DOORS = 115  # bit mask, see DOOR_BITS
MEASURE_FIRMWARE = 36
MEASURE_BATTERY_VOLTAGE = 39
MEASURE_ENGINE_HOURS_CAN = 41
MEASURE_FUEL_PERCENT_CAN = 46
MEASURE_VEHICLE_STATUS = 62  # 0 stopped, 1 running, 2 offline
MEASURE_ENGINE_HOURS = 71
MEASURE_FUEL_LITERS_OEM = 224

# Measure 115 (Canbus door status), from Runic's value semantics table.
DOOR_BITS = {
    "front_left": 0x100,
    "front_right": 0x200,
    "rear_left": 0x400,
    "rear_right": 0x800,
    "hood": 0x1000,
    "trunk": 0x2000,
}

PORTAL_URL = "https://portal.runic.io"

# Polling is only a fallback; alerts from the stream or webhooks push positions as they fire.
SCAN_INTERVAL = timedelta(minutes=5)

# A vehicle counts as moving only when its last GPS fix is recent and fast enough.
# Runic's own RUN/STOP status follows the device being active (heartbeats,
# ignition), so it reads RUN for parked vehicles too.
MOVING_MIN_SPEED_KMH = 5
MOVING_MAX_FIX_AGE = timedelta(minutes=15)

# Replay window recommended by the Runic webhook docs.
WEBHOOK_TOLERANCE_SECONDS = 300

# Alert types Runic can send (without the "alert." prefix), as listed in its webhook docs.
ALERT_TYPES = [
    "panic_button",
    "theft",
    "tow",
    "parking_moved",
    "jamming",
    "cable_cut",
    "tamper",
    "accident",
    "speed_limit",
    "poi_area",
    "movement",
    "input_activation",
    "device_alarm",
    "low_battery",
    "low_battery_internal",
    "offline",
    "fuel",
    "fuel_tank",
    "maintenance",
]
# Device triggers: every alert type, plus geofence enter and exit on their own.
TRIGGER_TYPES = [*ALERT_TYPES, "poi_enter", "poi_exit"]
