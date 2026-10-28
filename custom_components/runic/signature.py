"""Verify Runic webhooks (Standard Webhooks, HMAC-SHA256)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from collections.abc import Mapping

from .const import WEBHOOK_TOLERANCE_SECONDS


def verify_signature(
    secret: str, headers: Mapping[str, str], raw_body: bytes, now: float | None = None
) -> bool:
    """Return True if any v1 signature in the headers matches the body."""
    msg_id = headers.get("webhook-id")
    timestamp = headers.get("webhook-timestamp")
    signatures = headers.get("webhook-signature")
    if not (msg_id and timestamp and signatures):
        return False
    try:
        if abs((now or time.time()) - int(timestamp)) > WEBHOOK_TOLERANCE_SECONDS:
            return False
        key = base64.b64decode(secret.removeprefix("whsec_"))
    except ValueError:
        return False

    signed = f"{msg_id}.{timestamp}.".encode() + raw_body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    # Two space-separated values are sent during a secret rotation.
    return any(
        hmac.compare_digest(entry.split(",", 1)[1], expected)
        for entry in signatures.split()
        if entry.startswith("v1,")
    )
