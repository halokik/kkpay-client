"""KKPay compatible request and callback signing."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from typing import Any


def _signature_value(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def canonical_string(payload: Mapping[str, Any], api_token: str) -> str:
    """Return the exact string used by the legacy KKPay MD5 protocol."""
    if not api_token:
        raise ValueError("api_token must not be empty")
    pairs = (
        f"{key}={_signature_value(payload[key])}"
        for key in sorted(payload)
        if key != "signature" and payload[key] is not None and str(payload[key]) != ""
    )
    return "&".join(pairs) + api_token


def make_signature(payload: Mapping[str, Any], api_token: str) -> str:
    """Create a lowercase MD5 signature compatible with the KKPay gateway."""
    return hashlib.md5(canonical_string(payload, api_token).encode("utf-8")).hexdigest()


def verify_signature(payload: Mapping[str, Any], api_token: str) -> bool:
    """Verify a callback signature using constant-time comparison."""
    supplied = str(payload.get("signature") or "").strip().lower()
    if not supplied:
        return False
    expected = make_signature(payload, api_token)
    return hmac.compare_digest(supplied, expected)

