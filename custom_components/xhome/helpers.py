"""Small helpers shared by XHome entities."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Any

from xhome.client import JSON, unwrap_response


def unwrap_dict(payload: JSON | None) -> dict[str, Any]:
    """Unwrap an API response and return a dict when possible."""

    if payload is None:
        return {}
    value = unwrap_response(payload)
    return value if isinstance(value, dict) else {}


def device_uid(device: dict[str, Any]) -> str | None:
    """Return the stable XHome device UID."""

    return string_value(first_present(device, "uid", "uuid", "device_uid", "deviceUuid"))


def device_name(device: dict[str, Any]) -> str:
    """Return a human-friendly XHome device name."""

    return string_value(first_present(device, "name", "device_name", "remarkname", "title")) or "XHome device"


def device_model(device: dict[str, Any]) -> str | None:
    """Return the best available device model/type value."""

    return string_value(first_present(device, "model", "type", "device_type", "deviceType"))


def device_key(uid: str) -> str:
    """Return a stable non-reversible identifier for Home Assistant registries."""

    return hashlib.sha1(uid.encode("utf-8")).hexdigest()[:16]


def redact_uid(uid: str) -> str:
    """Return a short UID tail for diagnostics."""

    return f"...{uid[-6:]}" if len(uid) > 6 else "***"


def first_present(source: dict[str, Any], *keys: str) -> Any:
    """Return the first present, non-empty key from a dict."""

    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return value
    return None


def first_from_sources(sources: Iterable[dict[str, Any]], *keys: str) -> Any:
    """Return the first matching value from a set of dict sources."""

    for source in sources:
        value = first_present(source, *keys)
        if value not in (None, ""):
            return value
    return None


def string_value(value: Any) -> str | None:
    """Return a stripped string, or None for empty values."""

    if value in (None, ""):
        return None
    return str(value).strip()


def int_value(value: Any) -> int | None:
    """Return an int when the API value can be parsed."""

    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def bool_value(value: Any) -> bool | None:
    """Return a bool for common XHome flag values."""

    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "online", "success"}:
            return True
        if normalized in {"0", "false", "no", "off", "offline"}:
            return False
    return None

