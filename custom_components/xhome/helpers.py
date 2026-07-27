"""Small helpers shared by XHome entities."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

from .api.client import JSON, unwrap_response

DOORBELL_EVENT_TYPES = {"1"}
DOORBELL_TEXT_MARKERS = ("call", "doorbell", "door bell", "ding", "press", "ring", "visitor")
EVENT_LIST_KEYS = ("eventList", "oneList", "events", "data", "list", "recordList", "rows", "result")


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


def event_records(payload: JSON | None) -> list[dict[str, Any]]:
    """Extract event records from known XHome event response shapes."""

    if payload is None:
        return []
    return _event_records_from_value(unwrap_response(payload))


def event_key(uid: str, event: dict[str, Any]) -> str:
    """Build a stable event key for deduplication."""

    for key in ("event_guid", "eventGuid", "guid"):
        if value := string_value(event.get(key)):
            return f"{uid}:guid:{value}"
    for key in ("id", "event_id", "eventId"):
        if value := string_value(event.get(key)):
            return f"{uid}:id:{value}"

    event_type = string_value(event.get("type")) or "unknown"
    timestamp = string_value(first_present(event, "time_stamp", "timeStamp", "timestamp", "time"))
    if timestamp:
        return f"{uid}:ts:{timestamp}:{event_type}"

    fallback = json.dumps(event, sort_keys=True, default=str)
    return f"{uid}:hash:{hashlib.sha1(fallback.encode('utf-8')).hexdigest()}"


def is_doorbell_event(event: dict[str, Any]) -> bool:
    """Return True when an XHome event appears to be a doorbell/ring event."""

    event_type = string_value(event.get("type"))
    if event_type in DOORBELL_EVENT_TYPES:
        return True

    haystack = " ".join(
        string_value(event.get(key)) or ""
        for key in ("action", "event_type", "eventType", "type_name", "typeName", "info", "name", "remarks", "title")
    ).lower()
    return any(marker in haystack for marker in DOORBELL_TEXT_MARKERS)


def event_payload(device: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    """Build a redacted Home Assistant event payload."""

    uid = device_uid(device) or string_value(event.get("uid")) or ""
    return {
        "device_name": device_name(device),
        "device_id": int_value(first_present(device, "id", "device_id", "deviceId")),
        "uid_tail": redact_uid(uid) if uid else None,
        "event_guid": string_value(first_present(event, "event_guid", "eventGuid", "guid")),
        "event_id": string_value(first_present(event, "id", "event_id", "eventId")),
        "event_type": string_value(event.get("type")),
        "action": string_value(event.get("action")),
        "time": string_value(event.get("time")),
        "time_stamp": int_value(first_present(event, "time_stamp", "timeStamp", "timestamp")),
        "info": string_value(event.get("info")),
        "name": string_value(event.get("name")),
        "remarks": string_value(event.get("remarks")),
        "has_image": bool(string_value(event.get("img")) or string_value(event.get("m_oss_url"))),
        "video_status": int_value(event.get("video_status")),
        "video_size": int_value(event.get("video_size")),
    }


def _event_records_from_value(value: Any) -> list[dict[str, Any]]:
    """Recursively extract likely event dicts from a value."""

    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict) and _looks_like_event_record(item)]
    if not isinstance(value, dict):
        return []

    records: list[dict[str, Any]] = []
    for key in EVENT_LIST_KEYS:
        if key in value:
            records.extend(_event_records_from_value(value[key]))
    if records:
        return records
    return [value] if _looks_like_event_record(value) else []


def _looks_like_event_record(value: dict[str, Any]) -> bool:
    """Return True when a dict looks like an XHome event record."""

    return any(
        key in value
        for key in (
            "event_guid",
            "eventGuid",
            "time_stamp",
            "timeStamp",
            "img",
            "video_status",
            "m_oss_url",
        )
    ) and any(key in value for key in ("id", "type", "event_guid", "eventGuid"))
