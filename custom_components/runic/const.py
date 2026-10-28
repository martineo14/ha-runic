"""Constants for the Runic.io integration."""

from datetime import timedelta

DOMAIN = "runic"
EVENT_RUNIC = f"{DOMAIN}_event"

API_BASE = "https://api.runic.io/v1"

CONF_API_TOKEN = "api_token"
CONF_SIGNING_SECRET = "signing_secret"

# Polling is only a fallback; webhooks push positions as alerts fire.
SCAN_INTERVAL = timedelta(minutes=5)

# Replay window recommended by the Runic webhook docs.
WEBHOOK_TOLERANCE_SECONDS = 300
