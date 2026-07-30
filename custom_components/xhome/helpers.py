"""Small helpers shared by XHome entities."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import base64
import binascii
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlparse

from .api.client import JSON, unwrap_response

DOORBELL_EVENT_TYPES = {"1"}
DOORBELL_TEXT_MARKERS = ("call", "doorbell", "door bell", "ding", "press", "ring", "visitor")
EVENT_LIST_KEYS = ("eventList", "oneList", "events", "data", "list", "recordList", "rows", "result")
PUSH_EVENT_KINDS = {
    "0": "motion",
    "1": "doorbell",
    "2": "unlock",
    "3": "unlock",
    "4": "low_battery",
    "5": "lock_event",
    "6": "lock_event",
    "8": "temperature_alarm",
    "9": "temperature_alarm",
    "10": "sound_alarm",
    "11": "emergency",
    "12": "alarm",
    "13": "doorbell",
    "20": "offline",
    "21": "online",
    "100": "transfer",
    "200": "device_added",
    "201": "refused",
    "300": "server_update",
}
PUSH_EVENT_TYPE_NAMES = {
    "0": "pir",
    "1": "call",
    "2": "fingerprint_unlock",
    "3": "password_unlock",
    "4": "low_power",
    "5": "uart",
    "6": "lock",
    "8": "low_temperature_alarm",
    "9": "high_temperature_alarm",
    "10": "sound_alarm",
    "11": "emergency_call",
    "12": "stay_alarm",
    "13": "indoor_call",
    "20": "offline",
    "21": "online",
    "100": "transfer",
    "200": "add",
    "201": "refuse",
    "300": "server_update",
}
LOCK_EVENT_ENCODED_FIELDS = {
    "5": ("img",),
    "6": ("info",),
}
LOCK_EVENT_KINDS = {
    2: "unlock",
    3: "unlock",
    4: "unlock",
    6: "unlock",
    7: "low_battery",
    8: "alarm",
    9: "unlock",
    10: "alarm",
    11: "smoke_alarm",
    12: "gas_alarm",
    13: "emergency",
    14: "motion",
    15: "alarm",
    16: "alarm",
    17: "doorbell",
    18: "alarm",
    19: "lock",
    20: "lock",
    21: "unlock",
    22: "alarm",
    23: "unlock",
    24: "unlock",
    25: "tamper",
    26: "alarm",
    27: "alarm",
    28: "alarm",
    29: "unlock",
    30: "user_added",
    31: "user_deleted",
    32: "user_deleted",
    33: "lock",
    34: "mode_change",
    35: "mode_change",
    36: "unlock",
    37: "doorbell",
}
LOCK_EVENT_TYPE_NAMES = {
    0: "lock_pried",
    1: "forcible_unlock",
    2: "fingerprint_unlock_frozen",
    3: "password_unlock_frozen",
    4: "card_unlock_frozen",
    5: "key_unlock_frozen",
    6: "remote_unlock_frozen",
    7: "low_battery",
    8: "window_opened",
    9: "universal_key_unlock",
    10: "stay_timeout",
    11: "smoke_detected",
    12: "gas_leakage",
    13: "sos",
    14: "activity_alarm",
    15: "key_left_on_lock",
    16: "unlocked_alarm",
    17: "knocked_door",
    18: "loose_lockup",
    19: "door_locked",
    20: "locked",
    21: "unlock",
    22: "lock_fault",
    23: "face_unlock_frozen",
    24: "palm_unlock_frozen",
    25: "demolition_alarm",
    26: "hijacking_alarm",
    27: "device_information_change_alarm",
    28: "deployment_alarm",
    29: "finger_vein_unlock_frozen",
    30: "add_user",
    31: "delete_user",
    32: "clear_user",
    33: "remote_lock",
    34: "away_mode_on",
    35: "away_mode_off",
    36: "palm_vein_unlock_frozen",
    37: "doorbell",
}
LOCK_EVENT_UNLOCK_CONTENT_NAMES = {
    0: "fingerprint_unlock",
    1: "password_unlock",
    2: "card_unlock",
    3: "remote_control_unlock",
    4: "key_unlock",
    5: "iris_unlock",
    6: "palm_unlock",
    7: "finger_vein_unlock",
    8: "face_unlock",
    9: "app_unlock",
    10: "inside_unlock",
    11: "combination_unlock",
    12: "temporary_password_unlock",
    13: "mechanical_unlock",
    14: "palm_print_unlock",
    15: "virtual_password_unlock",
    17: "bluetooth_unlock",
}
LOCK_EVENT_LOCK_CONTENT_NAMES = {
    10: "inside_button_lock",
    19: "outside_button_lock",
}
LOCK_EVENT_USER_CONTENT_NAMES = {
    0: "fingerprint",
    1: "password",
    2: "card",
    3: "remote_control",
    4: "key",
    5: "iris",
    6: "palm",
    7: "finger_vein",
    8: "face",
    255: "all",
}
LOCK_EVENT_CONTENT_NAMES = {
    19: LOCK_EVENT_LOCK_CONTENT_NAMES,
    20: LOCK_EVENT_LOCK_CONTENT_NAMES,
    21: LOCK_EVENT_UNLOCK_CONTENT_NAMES,
    30: LOCK_EVENT_USER_CONTENT_NAMES,
    31: LOCK_EVENT_USER_CONTENT_NAMES,
    32: LOCK_EVENT_USER_CONTENT_NAMES,
}
ALARM_EVENT_KINDS = {"alarm", "emergency", "gas_alarm", "smoke_alarm", "sound_alarm", "tamper", "temperature_alarm"}
EVENT_TEXT_KIND_MARKERS = (
    ("unlock", ("unlock", "fingerprint", "password", "card", "remote control", "app remote")),
    ("doorbell", DOORBELL_TEXT_MARKERS),
    ("low_battery", ("low battery", "low power", "power alarm")),
    ("motion", ("motion", "pir", "activity")),
    ("tamper", ("tamper", "dismantle")),
    ("emergency", ("emergency", "hijack", "hijacking")),
    ("alarm", ("alarm", "smoke", "gas", "leak", "temperature", "sound", "noise")),
    ("offline", ("offline",)),
    ("online", ("online",)),
    ("lock", ("locked", "lock")),
)
MEDIA_LIST_KEYS = ("data", "files", "list", "media", "mediaList", "rows", "result")
MEDIA_URL_KEYS = ("oss_url", "m_oss_url", "img", "image", "image_url", "imageUrl", "url")
EVENT_GUID_KEYS = ("event_guid", "eventGuid", "guid")
NOTIFY_MASK_SENTINEL = 1


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


def notify_category_enabled(mask: int | None, event_ids: Iterable[int]) -> bool:
    """Return whether every XHome notification category is enabled.

    The app stores disabled notification categories in a bitmask. Bit 0 marks
    the mask as explicit; each disabled category uses bit ``event_id + 1``.
    A missing/zero mask means the app treats all categories as enabled.
    """

    if mask is None or mask & NOTIFY_MASK_SENTINEL == 0:
        return True
    return all(mask & _notify_event_bit(event_id) == 0 for event_id in event_ids)


def set_notify_category_enabled(mask: int | None, event_ids: Iterable[int], enabled: bool) -> int:
    """Return a new XHome notification bitmask with categories toggled."""

    new_mask = mask or NOTIFY_MASK_SENTINEL
    new_mask |= NOTIFY_MASK_SENTINEL
    for event_id in event_ids:
        bit = _notify_event_bit(event_id)
        if enabled:
            new_mask &= ~bit
        else:
            new_mask |= bit
    return new_mask


def event_records(payload: JSON | None) -> list[dict[str, Any]]:
    """Extract event records from known XHome event response shapes."""

    if payload is None:
        return []
    return _event_records_from_value(unwrap_response(payload))


def event_key(uid: str, event: dict[str, Any]) -> str:
    """Build a stable event key for deduplication."""

    for key in EVENT_GUID_KEYS:
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

    if event_kind(event) == "doorbell":
        return True

    event_type = string_value(event.get("type"))
    if event_type in DOORBELL_EVENT_TYPES:
        return True

    haystack = " ".join(
        string_value(event.get(key)) or ""
        for key in ("action", "event_type", "eventType", "type_name", "typeName", "info", "name", "remarks", "title")
    ).lower()
    return any(marker in haystack for marker in DOORBELL_TEXT_MARKERS)


def event_kind(event: dict[str, Any]) -> str:
    """Return a normalized kind for an XHome event."""

    if lock_kind := lock_event_kind(event):
        return lock_kind

    event_type = string_value(event.get("type"))
    if event_type and event_type in PUSH_EVENT_KINDS:
        return PUSH_EVENT_KINDS[event_type]

    haystack = _event_text_haystack(event)
    for kind, markers in EVENT_TEXT_KIND_MARKERS:
        if any(marker in haystack for marker in markers):
            return kind
    return "unknown"


def event_bus_types(event: dict[str, Any]) -> tuple[str, ...]:
    """Return specific Home Assistant event bus names for an XHome event."""

    kind = event_kind(event)
    if kind == "unknown":
        return ()

    event_types = [f"xhome_{kind}"]
    if kind in ALARM_EVENT_KINDS:
        event_types.append("xhome_alarm")
    return tuple(dict.fromkeys(event_types))


def lock_event_details(event: dict[str, Any]) -> dict[str, Any]:
    """Return decoded lock-event fields when the event embeds them."""

    event_type = string_value(event.get("type"))
    for key in LOCK_EVENT_ENCODED_FIELDS.get(event_type or "", ()):
        if details := _decode_base64_json(string_value(event.get(key))):
            lock_event_type = string_value(details.get("event_type"))
            lock_event_content = string_value(details.get("content"))
            lock_event_type_code = _hex_int(lock_event_type)
            lock_event_content_code = _hex_int(lock_event_content)
            return {
                "lock_event_type": lock_event_type,
                "lock_event_type_name": LOCK_EVENT_TYPE_NAMES.get(lock_event_type_code),
                "lock_event_content": lock_event_content,
                "lock_event_content_name": _lock_event_content_name(
                    lock_event_type_code,
                    lock_event_content_code,
                ),
                "lock_event_device": string_value(details.get("event_device")),
                "lock_event_user_id": string_value(details.get("user_id")),
                "lock_event_app_user": string_value(details.get("app_user")),
            }
    return {}


def lock_event_kind(event: dict[str, Any]) -> str | None:
    """Return a normalized kind from an embedded lock-event payload."""

    details = lock_event_details(event)
    lock_event_type = _hex_int(details.get("lock_event_type"))
    if lock_event_type is None:
        return None
    return LOCK_EVENT_KINDS.get(lock_event_type, "lock_event")


def event_payload(
    device: dict[str, Any],
    event: dict[str, Any],
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a redacted Home Assistant event payload."""

    uid = device_uid(device) or string_value(event.get("uid")) or ""
    lock_details = lock_event_details(event)
    lock_user = lock_user_mapping(uid, lock_details.get("lock_event_user_id"), options)
    payload = {
        "device_name": device_name(device),
        "device_id": int_value(first_present(device, "id", "device_id", "deviceId")),
        "uid_tail": redact_uid(uid) if uid else None,
        "event_guid": string_value(first_present(event, "event_guid", "eventGuid", "guid")),
        "event_id": string_value(first_present(event, "id", "event_id", "eventId")),
        "event_type": string_value(event.get("type")),
        "event_type_name": PUSH_EVENT_TYPE_NAMES.get(string_value(event.get("type")) or ""),
        "event_kind": event_kind(event),
        "action": string_value(event.get("action")),
        "time": string_value(event.get("time")),
        "time_stamp": int_value(first_present(event, "time_stamp", "timeStamp", "timestamp")),
        "info": string_value(event.get("info")),
        "name": string_value(event.get("name")),
        "remarks": string_value(event.get("remarks")),
        "has_image": bool(string_value(event.get("img")) or string_value(event.get("m_oss_url"))),
        "has_media": event_has_media(event),
        "video_status": int_value(event.get("video_status")),
        "video_size": int_value(event.get("video_size")),
    }
    payload.update(lock_details)
    payload.update(lock_user)
    return payload


def lock_user_mapping(
    uid: str | None,
    lock_event_user_id: Any,
    options: dict[str, Any] | None,
) -> dict[str, str]:
    """Return configured friendly lock-user attributes for a lock user id."""

    uid = string_value(uid)
    lock_user_id = string_value(lock_event_user_id)
    if not uid or not lock_user_id or not options:
        return {}

    mappings = options.get("lock_user_mappings")
    if not isinstance(mappings, dict):
        return {}

    for mapping in _lock_user_mappings_for_device(mappings, uid):
        ids = mapping.get("ids")
        if not isinstance(ids, list):
            continue
        normalized_ids = {string_value(item) for item in ids}
        if lock_user_id not in normalized_ids:
            continue
        result: dict[str, str] = {}
        if name := string_value(mapping.get("name")):
            result["lock_user_name"] = name
        if person := string_value(mapping.get("person")):
            result["lock_person"] = person
        return result
    return {}


def event_has_image(event: dict[str, Any]) -> bool:
    """Return True when an event has an image field or resolvable media URL."""

    return any(string_value(event.get(key)) for key in ("img", "m_oss_url"))


def event_has_media(event: dict[str, Any]) -> bool:
    """Return True when an event may have resolvable cloud media."""

    return event_has_image(event) or any(string_value(event.get(key)) for key in EVENT_GUID_KEYS)


def media_items(payload: JSON | None) -> list[dict[str, Any]]:
    """Extract media records from known XHome OSS response shapes."""

    if payload is None:
        return []
    return _media_items_from_value(unwrap_response(payload))


def first_media_item(payload: JSON | None) -> dict[str, Any] | None:
    """Return the first media item, preferring image-looking URLs."""

    items = media_items(payload)
    for item in items:
        url = media_url_from_item(item)
        if url and is_image_media(url, file_name=string_value(item.get("file_name"))):
            return item
    return items[0] if items else None


def first_video_media_item(payload: JSON | None) -> dict[str, Any] | None:
    """Return the first video-looking media item."""

    for item in media_items(payload):
        url = media_url_from_item(item)
        if url and is_video_media(url, file_name=string_value(item.get("file_name"))):
            return item
    return None


def media_url_from_event(event: dict[str, Any]) -> str | None:
    """Return a direct HTTP media URL from an event when present."""

    return _first_http_url(string_value(first_present(event, "m_oss_url", "img")))


def media_url_from_item(item: dict[str, Any]) -> str | None:
    """Return a direct HTTP media URL from an OSS media record."""

    for key in MEDIA_URL_KEYS:
        if url := _first_http_url(string_value(item.get(key))):
            return url
    return None


def guess_media_content_type(url: str, file_name: str | None = None) -> str | None:
    """Guess a media content type from a filename or URL path."""

    target = file_name or urlparse(url).path
    content_type, _ = mimetypes.guess_type(target)
    return content_type


def is_image_media(url: str, *, content_type: str | None = None, file_name: str | None = None) -> bool:
    """Return True when media metadata suggests an image."""

    guessed = content_type or guess_media_content_type(url, file_name)
    return guessed is not None and guessed.startswith("image/")


def is_video_media(url: str, *, content_type: str | None = None, file_name: str | None = None) -> bool:
    """Return True when media metadata suggests a video."""

    guessed = content_type or guess_media_content_type(url, file_name)
    return guessed is not None and guessed.startswith("video/")


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


def _media_items_from_value(value: Any) -> list[dict[str, Any]]:
    """Recursively extract likely media dicts from a value."""

    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict) and media_url_from_item(item)]
    if not isinstance(value, dict):
        return []

    records: list[dict[str, Any]] = []
    for key in MEDIA_LIST_KEYS:
        if key in value:
            records.extend(_media_items_from_value(value[key]))
    if records:
        return records
    return [value] if media_url_from_item(value) else []


def _first_http_url(value: str | None) -> str | None:
    """Return a URL only when it is directly fetchable."""

    if not value:
        return None
    for candidate in _url_candidates(value):
        parsed = urlparse(candidate)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return candidate
    return None


def _url_candidates(value: str) -> tuple[str, ...]:
    """Return raw and base64-decoded URL candidates."""

    normalized = value.strip()
    candidates = [normalized]
    try:
        padded = normalized + "=" * (-len(normalized) % 4)
        decoded = base64.b64decode(padded, validate=False).decode("utf-8").strip()
    except (binascii.Error, UnicodeDecodeError, ValueError):
        decoded = ""
    if decoded and decoded != normalized:
        candidates.append(decoded)
    return tuple(candidates)


def _event_text_haystack(event: dict[str, Any]) -> str:
    """Return searchable text for event classification."""

    return " ".join(
        string_value(event.get(key)) or ""
        for key in (
            "action",
            "event_type",
            "eventType",
            "type_name",
            "typeName",
            "info",
            "name",
            "remarks",
            "title",
            "message",
            "alert",
        )
    ).lower()


def _decode_base64_json(value: str | None) -> dict[str, Any] | None:
    """Decode a base64-encoded JSON object from the app's lock-event payload."""

    if not value:
        return None
    try:
        decoded = base64.b64decode(value, validate=False).decode("utf-8")
        parsed = json.loads(decoded)
    except (binascii.Error, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _hex_int(value: Any) -> int | None:
    """Return an int parsed from the app's hex event-code strings."""

    text = string_value(value)
    if text is None:
        return None
    try:
        return int(text, 16)
    except ValueError:
        return None


def _lock_event_content_name(event_type: int | None, content: int | None) -> str | None:
    """Return the app-derived name for a lock-event content code."""

    if event_type is None or content is None:
        return None
    return LOCK_EVENT_CONTENT_NAMES.get(event_type, {}).get(content)


def _lock_user_mappings_for_device(mappings: dict[str, Any], uid: str) -> list[dict[str, Any]]:
    """Return configured lock-user mappings for a device UID."""

    device_mappings = mappings.get(uid)
    if not isinstance(device_mappings, list):
        return []
    return [mapping for mapping in device_mappings if isinstance(mapping, dict)]


def _notify_event_bit(event_id: int) -> int:
    if event_id < 0 or event_id > 30:
        raise ValueError("event_id must be between 0 and 30")
    return 1 << (event_id + 1)
