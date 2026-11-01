"""Constants for the Runic.io integration."""

from datetime import timedelta

DOMAIN = "runic"
EVENT_RUNIC = f"{DOMAIN}_event"

API_BASE = "https://api.runic.io/v1"
STREAM_URL = "wss://api.runic.io/v1/alerts/stream"

CONF_API_TOKEN = "api_token"
CONF_SIGNING_SECRET = "signing_secret"
CONF_STREAM = "stream"

# Alert stream timings, from Runic's "Alert stream" docs: a connection silent for 2 minutes is closed.
STREAM_PING_INTERVAL = 30
STREAM_HELLO_TIMEOUT = 30
STREAM_BACKOFF_MAX = 300
STREAM_STABLE_AFTER = 60
SIGNAL_STREAM_STATE = f"{DOMAIN}_stream_state_{{}}"

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
