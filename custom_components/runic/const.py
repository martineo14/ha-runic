"""Constants for the Runic.io integration."""

from datetime import timedelta

DOMAIN = "runic"
EVENT_RUNIC = f"{DOMAIN}_event"

API_BASE = "https://api.runic.io/v1"

CONF_API_TOKEN = "api_token"
CONF_SIGNING_SECRET = "signing_secret"

# Polling is only a fallback; webhooks push positions as alerts fire.
SCAN_INTERVAL = timedelta(minutes=5)

# A vehicle counts as moving only when its last GPS fix is recent and fast enough.
# Runic's own RUN/STOP status follows the device being active (heartbeats,
# ignition), so it reads RUN for parked vehicles too.
MOVING_MIN_SPEED_KMH = 5
MOVING_MAX_FIX_AGE = timedelta(minutes=15)

# Replay window recommended by the Runic webhook docs.
WEBHOOK_TOLERANCE_SECONDS = 300
