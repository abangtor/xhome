"""Data coordinator for the XHome Home Assistant integration."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import timedelta
import logging
import time
from typing import Any

import requests
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import XHomeAPIError, XHomeAuthError, XHomeClient, XHomeError
from .api.client import JSON
from .const import (
    CONF_EVENT_SCAN_INTERVAL,
    CONF_REGION,
    CONF_SCAN_INTERVAL,
    CONF_TIMEOUT,
    DEFAULT_EVENT_SCAN_INTERVAL,
    DEFAULT_REGION,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TIMEOUT,
    DOMAIN,
    EVENT_XHOME_DOORBELL,
    EVENT_XHOME_EVENT,
    EVENT_XHOME_PREFIX,
)
from .helpers import (
    device_name,
    device_uid,
    event_bus_types,
    event_has_image,
    event_key,
    event_payload,
    event_records,
    first_media_item,
    first_present,
    first_from_sources,
    guess_media_content_type,
    int_value,
    is_doorbell_event,
    media_url_from_event,
    media_url_from_item,
    string_value,
    unwrap_dict,
)

LOGGER = logging.getLogger(__name__)
MAX_SEEN_EVENT_KEYS = 500


@dataclass(slots=True)
class XHomeDeviceRuntimeData:
    """Combined cloud state for one XHome device."""

    uid: str
    device: dict[str, Any]
    detail: dict[str, Any] = field(default_factory=dict)
    online: dict[str, Any] = field(default_factory=dict)
    screen_light: dict[str, Any] = field(default_factory=dict)
    firmware: dict[str, Any] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def name(self) -> str:
        """Return the device name."""

        return device_name(self.device)

    @property
    def model(self) -> str | None:
        """Return the best model/type value."""

        return string_value(self.first("model", "type", "device_type", "deviceType"))

    @property
    def device_id(self) -> int | None:
        """Return the cloud device id when present."""

        return int_value(self.first("id", "device_id", "deviceId"))

    def first(self, *keys: str) -> Any:
        """Return a value from detail, device, online, screen, then firmware data."""

        return first_from_sources(
            (self.detail, self.device, self.online, self.screen_light, self.firmware),
            *keys,
        )


@dataclass(slots=True)
class XHomeCoordinatorData:
    """Top-level coordinator payload."""

    devices: dict[str, XHomeDeviceRuntimeData] = field(default_factory=dict)


@dataclass(slots=True)
class XHomeLatestEventMedia:
    """Resolved media for the latest image-bearing XHome event."""

    uid: str
    event_key: str
    event_guid: str | None
    event_id: str | None
    event_type: str | None
    time: str | None
    time_stamp: int | None
    url: str
    file_name: str | None = None
    exp_time: int | None = None
    content_type: str | None = None
    video_status: int | None = None
    video_size: int | None = None


@dataclass(slots=True)
class XHomeLatestEvent:
    """Latest XHome event for one device."""

    uid: str
    event_key: str
    sort_key: tuple[int, str]
    payload: dict[str, Any]


class XHomeDataUpdateCoordinator(DataUpdateCoordinator[XHomeCoordinatorData]):
    """Coordinate cloud polling for one XHome account."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize the coordinator."""

        self.config_entry = config_entry
        self.client = XHomeClient(
            region=config_entry.data.get(CONF_REGION, DEFAULT_REGION),
            timeout=config_entry.options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
        )
        self._seen_event_keys: set[str] = set()
        self._seen_event_order: deque[str] = deque()
        self._event_poll_seeded = False
        self._latest_events: dict[str, XHomeLatestEvent] = {}
        self._latest_event_media: dict[str, XHomeLatestEventMedia] = {}
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )

    def async_start_event_polling(self) -> Any:
        """Start polling for new XHome events."""

        return async_track_time_interval(
            self.hass,
            self._async_event_poll_tick,
            timedelta(seconds=self.config_entry.options.get(CONF_EVENT_SCAN_INTERVAL, DEFAULT_EVENT_SCAN_INTERVAL)),
        )

    async def async_seed_events(self) -> None:
        """Seed the event dedupe cache without firing historical events."""

        await self.async_poll_events(seed_only=True)

    def latest_event_media(self, uid: str) -> XHomeLatestEventMedia | None:
        """Return cached latest event media for a device."""

        return self._latest_event_media.get(uid)

    def latest_event(self, uid: str) -> XHomeLatestEvent | None:
        """Return cached latest event for a device."""

        return self._latest_events.get(uid)

    async def _async_update_data(self) -> XHomeCoordinatorData:
        """Fetch the latest data from XHome."""

        try:
            return await self.hass.async_add_executor_job(self._update_data)
        except XHomeAuthError as err:
            self.client.token = None
            raise ConfigEntryAuthFailed("XHome authentication failed") from err
        except (XHomeAPIError, XHomeError, requests.RequestException, TimeoutError, ValueError) as err:
            raise UpdateFailed(f"XHome update failed: {err}") from err

    async def async_unlock_device(self, uid: str) -> JSON:
        """Unlock a door device through the XHome cloud."""

        try:
            return await self.hass.async_add_executor_job(self._unlock_device, uid)
        except XHomeAuthError as err:
            self.client.token = None
            raise HomeAssistantError("XHome authentication failed while unlocking") from err
        except (XHomeAPIError, XHomeError, requests.RequestException, TimeoutError, ValueError) as err:
            raise HomeAssistantError(f"XHome unlock failed: {err}") from err

    async def async_poll_events(self, *, seed_only: bool = False) -> None:
        """Poll the XHome event endpoint and fire Home Assistant events."""

        if self.data is None or not self.data.devices:
            return
        seed_only = seed_only or not self._event_poll_seeded

        try:
            events = await self.hass.async_add_executor_job(self._poll_device_events)
        except XHomeAuthError as err:
            self.client.token = None
            LOGGER.warning("XHome event polling authentication failed: %s", err)
            return
        except (XHomeAPIError, XHomeError, requests.RequestException, TimeoutError, ValueError) as err:
            LOGGER.debug("XHome event polling failed: %s", err)
            return

        event_candidates: dict[str, dict[str, Any]] = {}
        media_candidates: dict[str, dict[str, Any]] = {}
        for event in sorted(events, key=_event_sort_key):
            key = event["event_key"]
            is_new = self._remember_event_key(key)
            if is_new:
                _keep_newest_event_candidate(event_candidates, event)
            if is_new and event["has_image"]:
                _keep_newest_media_candidate(media_candidates, event)
            if not is_new or seed_only:
                continue

            payload = event["payload"]
            self.hass.bus.async_fire(EVENT_XHOME_EVENT, payload)
            for event_type in event["bus_event_types"]:
                self.hass.bus.async_fire(event_type, payload)

        updated = self._update_latest_events(event_candidates.values())
        if await self._async_update_latest_event_media(media_candidates.values()):
            updated = True
        if updated:
            self.async_update_listeners()
        self._event_poll_seeded = True

    async def async_get_latest_event_image(self, uid: str) -> bytes | None:
        """Return latest event image bytes for a device."""

        try:
            return await self.hass.async_add_executor_job(self._download_latest_event_image, uid)
        except (XHomeAPIError, XHomeError, requests.RequestException, TimeoutError, ValueError) as err:
            LOGGER.debug("XHome latest event image fetch failed: %s", err)
            return None

    def _unlock_device(self, uid: str) -> JSON:
        """Synchronous unlock helper."""

        self._ensure_login()
        return self.client.unlock_door(uid)

    async def _async_event_poll_tick(self, now: Any) -> None:
        """Handle a scheduled event polling tick."""

        await self.async_poll_events()

    def _update_data(self) -> XHomeCoordinatorData:
        """Synchronous data update helper."""

        self._ensure_login()
        payload = self.client.list_all_devices()
        devices = self.client.flatten_devices(payload)
        runtime_devices: dict[str, XHomeDeviceRuntimeData] = {}

        for device in devices:
            uid = device_uid(device)
            if not uid:
                continue

            runtime_devices[uid] = XHomeDeviceRuntimeData(
                uid=uid,
                device=device,
                detail=self._optional_dict("detail", self.client.get_device_detail, uid),
                online=self._optional_dict(
                    "online",
                    self.client.get_online_status,
                    uid,
                    device.get("online_type") or device.get("onlineType") or "1",
                ),
                screen_light=self._optional_dict("screen_light", self.client.get_screen_light_config, uid),
                firmware=self._optional_dict("firmware", self.client.get_firmware, uid),
            )

        return XHomeCoordinatorData(devices=runtime_devices)

    def _poll_device_events(self) -> list[dict[str, Any]]:
        """Fetch recent event records for all known devices."""

        self._ensure_login()
        if self.data is None:
            return []

        events: list[dict[str, Any]] = []
        for data in self.data.devices.values():
            device_type = string_value(data.first("type", "model", "device_type", "deviceType")) or "9"
            payload = self.client.get_new_device_events(data.uid, device_type)
            for record in event_records(payload):
                key = event_key(data.uid, record)
                payload = {
                    **event_payload(data.device, record),
                    "event_key": key,
                }
                events.append(
                    {
                        "uid": data.uid,
                        "event_key": key,
                        "doorbell": is_doorbell_event(record),
                        "has_image": event_has_image(record),
                        "record": record,
                        "sort_key": _record_sort_key(record),
                        "payload": payload,
                        "bus_event_types": _event_bus_types(record),
                    }
                )
        return events

    def _update_latest_events(self, events: Iterable[dict[str, Any]]) -> bool:
        """Cache the latest event per device."""

        updated = False
        for event in events:
            latest = XHomeLatestEvent(
                uid=event["uid"],
                event_key=event["event_key"],
                sort_key=event["sort_key"],
                payload=event["payload"],
            )
            current = self._latest_events.get(latest.uid)
            if current is not None and current.sort_key > latest.sort_key:
                continue
            self._latest_events[latest.uid] = latest
            updated = True
        return updated

    async def _async_update_latest_event_media(self, events: Iterable[dict[str, Any]]) -> bool:
        """Resolve and cache latest event media from event candidates."""

        updated = False
        for event in events:
            try:
                media = await self.hass.async_add_executor_job(self._resolve_event_media, event)
            except XHomeAuthError as err:
                self.client.token = None
                LOGGER.warning("XHome event media authentication failed: %s", err)
                continue
            except (XHomeAPIError, XHomeError, requests.RequestException, TimeoutError, ValueError) as err:
                LOGGER.debug("Skipping XHome event media for %s: %s", event.get("event_key"), err)
                continue
            if media is None:
                continue
            current = self._latest_event_media.get(media.uid)
            if current is not None and _media_sort_key(current) > _media_sort_key(media):
                continue
            self._latest_event_media[media.uid] = media
            updated = True
        return updated

    def _resolve_event_media(self, event: dict[str, Any]) -> XHomeLatestEventMedia | None:
        """Resolve a signed media URL for an event."""

        self._ensure_login()

        uid = event["uid"]
        record = event["record"]
        event_guid = string_value(first_present(record, "event_guid", "eventGuid", "guid"))
        media_item: dict[str, Any] | None = None
        url = media_url_from_event(record)

        if url is None and event_guid:
            media_payload = self.client.get_media_url(uid, event_guid)
            media_item = first_media_item(media_payload)
            if media_item is not None:
                url = media_url_from_item(media_item)
        if url is None:
            return None

        file_name = string_value(media_item.get("file_name")) if media_item else None
        return XHomeLatestEventMedia(
            uid=uid,
            event_key=event["event_key"],
            event_guid=event_guid,
            event_id=string_value(first_present(record, "id", "event_id", "eventId")),
            event_type=string_value(record.get("type")),
            time=string_value(record.get("time")),
            time_stamp=int_value(first_present(record, "time_stamp", "timeStamp", "timestamp")),
            url=url,
            file_name=file_name,
            exp_time=int_value(media_item.get("exp_time")) if media_item else None,
            content_type=guess_media_content_type(url, file_name),
            video_status=int_value(record.get("video_status")),
            video_size=int_value(record.get("video_size")),
        )

    def _download_latest_event_image(self, uid: str) -> bytes | None:
        """Download latest event image bytes from the cached signed URL."""

        media = self._latest_event_media.get(uid)
        if media is None:
            return None

        if _media_url_expired(media):
            self._refresh_event_media_url(media)
        response = self.client.session.get(media.url, timeout=self.client.timeout)
        response.raise_for_status()
        response_content_type = _response_content_type(response)
        content_type = response_content_type or media.content_type
        if content_type is not None and not content_type.startswith("image/"):
            media.content_type = content_type
            return None
        media.content_type = content_type or "image/jpeg"
        return response.content or None

    def _refresh_event_media_url(self, media: XHomeLatestEventMedia) -> None:
        """Refresh an expired signed media URL when possible."""

        if not media.event_guid:
            return

        self._ensure_login()
        media_payload = self.client.get_media_url(media.uid, media.event_guid)
        media_item = first_media_item(media_payload)
        if media_item is None:
            return

        url = media_url_from_item(media_item)
        if url is None:
            return

        file_name = string_value(media_item.get("file_name"))
        media.url = url
        media.file_name = file_name
        media.exp_time = int_value(media_item.get("exp_time"))
        media.content_type = guess_media_content_type(url, file_name)

    def _ensure_login(self) -> None:
        """Log in when the client does not already have a token."""

        if self.client.token:
            return
        self.client.login(
            self.config_entry.data[CONF_USERNAME],
            self.config_entry.data[CONF_PASSWORD],
        )

    def _optional_dict(self, name: str, func: Any, *args: Any) -> dict[str, Any]:
        """Fetch optional per-device data without failing the whole coordinator."""

        try:
            return unwrap_dict(func(*args))
        except (XHomeAPIError, XHomeError, requests.RequestException, TimeoutError, ValueError) as err:
            LOGGER.debug("Skipping optional XHome %s payload: %s", name, err)
            return {}

    def _remember_event_key(self, key: str) -> bool:
        """Remember an event key and return True if it is new."""

        if key in self._seen_event_keys:
            return False
        while len(self._seen_event_order) >= MAX_SEEN_EVENT_KEYS:
            self._seen_event_keys.discard(self._seen_event_order.popleft())
        self._seen_event_order.append(key)
        self._seen_event_keys.add(key)
        return True


def _record_sort_key(record: dict[str, Any]) -> tuple[int, str]:
    """Return a stable event ordering key."""

    timestamp = int_value(first_present(record, "time_stamp", "timeStamp", "timestamp")) or 0
    event_id = string_value(first_present(record, "id", "event_id", "eventId", "event_guid", "eventGuid")) or ""
    return (timestamp, event_id)


def _event_sort_key(event: dict[str, Any]) -> tuple[int, str]:
    """Return a stable event ordering key for coordinator event wrappers."""

    return event["sort_key"]


def _media_sort_key(media: XHomeLatestEventMedia) -> tuple[int, str]:
    """Return a stable ordering key for cached media."""

    return (media.time_stamp or 0, media.event_id or media.event_guid or media.event_key)


def _keep_newest_media_candidate(candidates: dict[str, dict[str, Any]], event: dict[str, Any]) -> None:
    """Keep only the newest media candidate for each device in a poll."""

    current = candidates.get(event["uid"])
    if current is None or event["sort_key"] >= current["sort_key"]:
        candidates[event["uid"]] = event


def _keep_newest_event_candidate(candidates: dict[str, dict[str, Any]], event: dict[str, Any]) -> None:
    """Keep only the newest event candidate for each device in a poll."""

    current = candidates.get(event["uid"])
    if current is None or event["sort_key"] >= current["sort_key"]:
        candidates[event["uid"]] = event


def _event_bus_types(record: dict[str, Any]) -> tuple[str, ...]:
    """Return specific bus event names, preserving the existing doorbell event."""

    event_types = list(event_bus_types(record))
    if is_doorbell_event(record) and EVENT_XHOME_DOORBELL not in event_types:
        event_types.append(EVENT_XHOME_DOORBELL)
    return tuple(event_type for event_type in event_types if event_type.startswith(EVENT_XHOME_PREFIX))


def _response_content_type(response: requests.Response) -> str | None:
    """Return a normalized response content type."""

    content_type = response.headers.get("Content-Type")
    if not content_type:
        return None
    return content_type.split(";", 1)[0].strip().lower()


def _media_url_expired(media: XHomeLatestEventMedia) -> bool:
    """Return True when a signed media URL appears expired or nearly expired."""

    return media.exp_time is not None and media.exp_time <= int(time.time()) + 60
