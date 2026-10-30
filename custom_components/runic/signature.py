"""Verify Runic webhooks (Standard Webhooks, HMAC-SHA256)."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import time
from collections.abc import Mapping

from .const import WEBHOOK_TOLERANCE_SECONDS


def signature_problem(
    secret: str, headers: Mapping[str, str], raw_body: bytes, now: float | None = None
) -> str | None:
    """Return why the request fails verification, or None if it is genuine."""
    msg_id = headers.get("webhook-id")
    timestamp = headers.get("webhook-timestamp")
    signatures = headers.get("webhook-signature")
    if not (msg_id and timestamp and signatures):
        return "the webhook-id, webhook-timestamp or webhook-signature header is missing"
    try:
        drift = (now or time.time()) - int(timestamp)
    except ValueError:
        return f"the timestamp {timestamp!r} is not a number"
    if abs(drift) > WEBHOOK_TOLERANCE_SECONDS:
        return (
            f"the timestamp is {abs(drift):.0f} s off this machine's clock "
            f"(limit {WEBHOOK_TOLERANCE_SECONDS} s): check Home Assistant's time"
        )
    try:
        key = base64.b64decode(secret.removeprefix("whsec_"), validate=True)
    except (binascii.Error, ValueError):
        return "the signing secret saved in Home Assistant is not a valid whsec_ secret"

    signed = f"{msg_id}.{timestamp}.".encode() + raw_body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    # Two space-separated values are sent during a secret rotation.
    if any(
        hmac.compare_digest(entry.split(",", 1)[1], expected)
        for entry in signatures.split()
        if entry.startswith("v1,")
    ):
        return None
    return "the signature doesn't match the signing secret saved in Home Assistant"


def verify_signature(
    secret: str, headers: Mapping[str, str], raw_body: bytes, now: float | None = None
) -> bool:
    """Return True if any v1 signature in the headers matches the body."""
    return signature_problem(secret, headers, raw_body, now) is None
