"""Data coordinator for the XHome Home Assistant integration."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import timedelta
import logging
import mimetypes
from pathlib import Path
import re
from threading import Event, Thread
import time
from typing import Any
from urllib.parse import urlparse

import requests
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import XHomeAPIError, XHomeAuthError, XHomeClient, XHomeError
from .api.client import JSON
from .api.constants import normalize_region
from .api.exceptions import XHomePushError
from .api.push import XHomePushClient, XHomePushMessage, build_push_register_info
from .const import (
    CONF_EVENT_SCAN_INTERVAL,
    CONF_LOCAL_PUSH_ENABLED,
    CONF_REGION,
    CONF_SCAN_INTERVAL,
    CONF_TIMEOUT,
    DEFAULT_EVENT_SCAN_INTERVAL,
    DEFAULT_LOCAL_PUSH_ENABLED,
    DEFAULT_REGION,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TIMEOUT,
    DOMAIN,
    EVENT_XHOME_DOORBELL,
    EVENT_XHOME_EVENT,
    EVENT_XHOME_PREFIX,
)
from .helpers import (
    device_key,
    device_name,
    device_uid,
    event_bus_types,
    event_has_media,
    event_has_image,
    event_key,
    event_payload,
    event_records,
    first_from_sources,
    first_present,
    guess_media_content_type,
    int_value,
    is_image_media,
    is_doorbell_event,
    is_video_media,
    media_url_from_event,
    media_url_from_item,
    media_items,
    set_notify_category_enabled,
    string_value,
    unwrap_dict,
)

LOGGER = logging.getLogger(__name__)
MAX_SEEN_EVENT_KEYS = 500
MEDIA_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


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
    """Resolved media for the latest XHome event."""

    uid: str
    event_key: str
    event_guid: str | None
    event_id: str | None
    event_type: str | None
    time: str | None
    time_stamp: int | None
    url: str
    media_kind: str = "unknown"
    file_name: str | None = None
    exp_time: int | None = None
    content_type: str | None = None
    video_status: int | None = None
    video_size: int | None = None


@dataclass(slots=True)
class XHomeDownloadedEventMedia:
    """Local files downloaded from the latest XHome event media."""

    uid: str
    event_key: str
    saved_at: int
    media_count: int = 0
    image_path: str | None = None
    video_path: str | None = None


@dataclass(slots=True)
class XHomeLatestEvent:
    """Latest XHome event for one device."""

    uid: str
    event_key: str
    sort_key: tuple[int, str]
    payload: dict[str, Any]


@dataclass(slots=True)
class XHomeLiveStreamSession:
    """Metadata needed by an external XHome live-stream sidecar."""

    uid: str
    device_id: int | None
    model: str | None
    native_iot_host: str
    token: str
    token_payload: JSON
    start_command: int = 20
    stop_command: int = 21
    audio_codec: str = "g711"
    video_codec: str = "h264"
    media_header_bytes: int = 40


class XHomeDataUpdateCoordinator(DataUpdateCoordinator[XHomeCoordinatorData]):
    """Coordinate cloud polling for one XHome account."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize the coordinator."""

        self.config_entry = config_entry
        self.client = XHomeClient(
            region=_entry_region(config_entry),
            timeout=config_entry.options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
        )
        self._seen_event_keys: set[str] = set()
        self._seen_event_order: deque[str] = deque()
        self._event_poll_seeded = False
        self._latest_events: dict[str, XHomeLatestEvent] = {}
        self._latest_event_media: dict[str, XHomeLatestEventMedia] = {}
        self._latest_event_video_media: dict[str, XHomeLatestEventMedia] = {}
        self._downloaded_event_media: dict[str, XHomeDownloadedEventMedia] = {}
        self._local_push_client: XHomePushClient | None = None
        self._local_push_registered_token: str | None = None
        self._local_push_status: dict[str, Any] = {
            "enabled": config_entry.options.get(CONF_LOCAL_PUSH_ENABLED, DEFAULT_LOCAL_PUSH_ENABLED),
            "running": False,
            "connected": False,
            "registered": False,
            "frames": 0,
            "tokens": 0,
            "events": 0,
            "reconnects": 0,
            "last_error": None,
            "last_frame_command": None,
            "last_frame_kind": None,
            "last_frame_at": None,
            "last_event_at": None,
            "registered_token_tail": None,
        }
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

    def async_start_local_push(self) -> Callable[[], None]:
        """Start the native push socket listener."""

        if not self.config_entry.options.get(CONF_LOCAL_PUSH_ENABLED, DEFAULT_LOCAL_PUSH_ENABLED):
            self._local_push_status.update({"enabled": False, "running": False, "connected": False})
            return _noop

        self._local_push_status.update({"enabled": True, "running": True, "last_error": None})
        stop_event = Event()
        thread = Thread(
            target=self._local_push_worker,
            args=(stop_event,),
            name=f"xhome-local-push-{self.config_entry.entry_id}",
            daemon=True,
        )
        thread.start()

        def stop_local_push() -> None:
            stop_event.set()
            if self._local_push_client is not None:
                self._local_push_client.close()
            thread.join(timeout=2)
            self._local_push_status.update({"running": False, "connected": False})

        return stop_local_push

    def local_push_status(self) -> dict[str, Any]:
        """Return local push worker status and counters."""

        return dict(self._local_push_status)

    async def async_seed_events(self) -> None:
        """Seed the event dedupe cache without firing historical events."""

        await self.async_poll_events(seed_only=True)

    def latest_event_media(self, uid: str) -> XHomeLatestEventMedia | None:
        """Return cached latest event media for a device."""

        return self._latest_event_media.get(uid)

    def latest_event(self, uid: str) -> XHomeLatestEvent | None:
        """Return cached latest event for a device."""

        return self._latest_events.get(uid)

    def latest_event_video_media(self, uid: str) -> XHomeLatestEventMedia | None:
        """Return cached latest event video media for a device."""

        return self._latest_event_video_media.get(uid)

    def downloaded_event_media(self, uid: str) -> XHomeDownloadedEventMedia | None:
        """Return the latest local media download for a device."""

        return self._downloaded_event_media.get(uid)

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

    async def async_set_push_enabled(self, uid: str, enabled: bool) -> JSON:
        """Set the main XHome push notification switch."""

        return await self._async_update_device_setting("push notifications", self._set_push_enabled, uid, enabled)

    async def async_set_offline_notifications(self, uid: str, enabled: bool) -> JSON:
        """Set the XHome offline notification switch."""

        return await self._async_update_device_setting(
            "offline notifications",
            self._set_offline_notifications,
            uid,
            enabled,
        )

    async def async_set_notification_category(self, uid: str, event_ids: tuple[int, ...], enabled: bool) -> JSON:
        """Set one XHome notification category in the notify_ctrl mask."""

        return await self._async_update_device_setting(
            "notification category",
            self._set_notification_category,
            uid,
            event_ids,
            enabled,
        )

    async def async_set_battery_display(self, uid: str, enabled: bool) -> JSON:
        """Set the door screen battery-display switch."""

        return await self._async_update_device_setting(
            "battery display",
            self.client.set_battery_display,
            uid,
            enabled,
        )

    async def async_set_wet_play(self, uid: str, enabled: bool) -> JSON:
        """Set the weather forecast voice/display switch."""

        return await self._async_update_device_setting("weather forecast", self.client.set_wet_play, uid, enabled)

    async def async_set_call_screen(self, uid: str, enabled: bool) -> JSON:
        """Set whether a doorbell call wakes the screen."""

        return await self._async_update_device_setting("call screen", self.client.set_call_screen, uid, enabled)

    async def async_set_remote_unlock_anytime(self, uid: str, enabled: bool) -> JSON:
        """Set whether remote unlock is allowed anytime or only after ringing."""

        unlock_limit = 0 if enabled else 1
        return await self._async_update_device_setting(
            "remote unlock limit",
            self.client.set_device_unlock_limit,
            uid,
            unlock_limit,
        )

    async def async_set_screen_timeout(self, uid: str, timeout_seconds: int) -> JSON:
        """Set the door screen auto-off timeout."""

        return await self._async_update_device_setting(
            "screen timeout",
            self.client.set_screen_light_timeout,
            uid,
            timeout_seconds,
        )

    async def async_set_standby_mode(self, uid: str, standby_mode: int) -> JSON:
        """Set the XHome standby mode."""

        return await self._async_update_device_setting("standby mode", self.client.set_standby_mode, uid, standby_mode)

    async def async_set_target_ev(self, uid: str, target_ev: int) -> JSON:
        """Set the night-vision target EV value."""

        return await self._async_update_device_setting(
            "night vision target EV",
            self.client.set_target_ev,
            uid,
            target_ev,
        )

    async def async_call_client(
        self,
        name: str,
        method_name: str,
        *args: Any,
        refresh: bool = False,
        **kwargs: Any,
    ) -> JSON:
        """Call a synchronous API client method through Home Assistant's executor."""

        try:
            result = await self.hass.async_add_executor_job(
                self._call_client_method,
                method_name,
                args,
                kwargs,
            )
        except XHomeAuthError as err:
            self.client.token = None
            raise HomeAssistantError(f"XHome authentication failed while calling {name}") from err
        except (XHomeAPIError, XHomeError, requests.RequestException, TimeoutError, ValueError) as err:
            raise HomeAssistantError(f"XHome {name} failed: {err}") from err

        if refresh:
            await self.async_request_refresh()
        return result

    async def async_prepare_live_stream(self, uid: str) -> XHomeLiveStreamSession:
        """Return live-token and native transport metadata for one device."""

        try:
            return await self.hass.async_add_executor_job(self._prepare_live_stream, uid)
        except XHomeAuthError as err:
            self.client.token = None
            raise HomeAssistantError("XHome authentication failed while preparing live stream") from err
        except (XHomeAPIError, XHomeError, requests.RequestException, TimeoutError, ValueError) as err:
            raise HomeAssistantError(f"XHome live stream preparation failed: {err}") from err

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
        media_candidates: list[dict[str, Any]] = []
        for event in sorted(events, key=_event_sort_key):
            key = event["event_key"]
            is_new = self._remember_event_key(key)
            if is_new:
                _keep_newest_event_candidate(event_candidates, event)
            if is_new and event["has_media"]:
                media_candidates.append(event)
            if not is_new or seed_only:
                continue

            self._fire_event_bus_events(event)

        updated = self._update_latest_events(event_candidates.values())
        if await self._async_update_latest_event_media(media_candidates):
            updated = True
        if updated:
            self.async_update_listeners()
        self._event_poll_seeded = True

    async def async_handle_local_push_event(self, record: dict[str, Any]) -> None:
        """Handle one event received from the native push socket."""

        if self.data is None:
            return

        uid = string_value(first_present(record, "uid", "uuid", "device"))
        if not uid:
            LOGGER.debug("Skipping XHome local push event without uid")
            return

        data = self.data.devices.get(uid)
        device = data.device if data is not None else {"uid": uid, "name": string_value(record.get("name"))}
        event = self._event_from_record(uid, device, record, source="local_push")
        if not self._remember_event_key(event["event_key"]):
            return

        self._fire_event_bus_events(event)
        updated = self._update_latest_events([event])
        if event["has_media"] and await self._async_update_latest_event_media([event]):
            updated = True
        if updated:
            self.async_update_listeners()

    async def async_refresh_latest_event_media(self, uid: str) -> bool:
        """Refresh latest event and media caches for one device."""

        if self.data is None or uid not in self.data.devices:
            raise HomeAssistantError("XHome device is unavailable")

        try:
            events = await self.hass.async_add_executor_job(self._poll_device_events_for_uids, {uid})
        except XHomeAuthError as err:
            self.client.token = None
            raise HomeAssistantError("XHome authentication failed while refreshing event media") from err
        except (XHomeAPIError, XHomeError, requests.RequestException, TimeoutError, ValueError) as err:
            raise HomeAssistantError(f"XHome event media refresh failed: {err}") from err

        event_candidates: dict[str, dict[str, Any]] = {}
        media_candidates: list[dict[str, Any]] = []
        for event in sorted(events, key=_event_sort_key):
            _keep_newest_event_candidate(event_candidates, event)
            if event["has_media"]:
                media_candidates.append(event)

        updated = self._update_latest_events(event_candidates.values())
        if await self._async_update_latest_event_media(media_candidates):
            updated = True
        if updated:
            self.async_update_listeners()
        return updated

    async def async_download_latest_event_media(self, uid: str) -> XHomeDownloadedEventMedia | None:
        """Fetch and save the latest event image/video media to the HA media folder."""

        await self.async_refresh_latest_event_media(uid)
        try:
            downloaded = await self.hass.async_add_executor_job(self._download_latest_event_media_files, uid)
        except XHomeAuthError as err:
            self.client.token = None
            raise HomeAssistantError("XHome authentication failed while downloading event media") from err
        except (XHomeAPIError, XHomeError, requests.RequestException, TimeoutError, ValueError, OSError) as err:
            raise HomeAssistantError(f"XHome event media download failed: {err}") from err

        if downloaded is not None:
            self._downloaded_event_media[uid] = downloaded
            self.async_update_listeners()
        return downloaded

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

    async def _async_update_device_setting(self, name: str, func: Any, *args: Any) -> JSON:
        """Run a synchronous setting update and refresh coordinator data."""

        try:
            result = await self.hass.async_add_executor_job(self._call_device_setting, func, *args)
        except XHomeAuthError as err:
            self.client.token = None
            raise HomeAssistantError(f"XHome authentication failed while updating {name}") from err
        except (XHomeAPIError, XHomeError, requests.RequestException, TimeoutError, ValueError) as err:
            raise HomeAssistantError(f"XHome {name} update failed: {err}") from err

        await self.async_request_refresh()
        return result

    def _call_device_setting(self, func: Any, *args: Any) -> JSON:
        """Call a setting setter after ensuring cloud login."""

        self._ensure_login()
        return func(*args)

    def _call_client_method(self, method_name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> JSON:
        """Call one API client method after ensuring cloud login."""

        self._ensure_login()
        method = getattr(self.client, method_name)
        return method(*args, **kwargs)

    def _set_push_enabled(self, uid: str, enabled: bool) -> JSON:
        """Synchronous helper for the main push notification switch."""

        data = self._device_for_setting(uid)
        return self._update_device_push_fields(data, push=_flag(enabled))

    def _set_offline_notifications(self, uid: str, enabled: bool) -> JSON:
        """Synchronous helper for the offline notification switch."""

        data = self._device_for_setting(uid)
        return self._update_device_push_fields(data, ispush=_flag(enabled))

    def _set_notification_category(self, uid: str, event_ids: tuple[int, ...], enabled: bool) -> JSON:
        """Synchronous helper for the XHome notification category bitmask."""

        data = self._device_for_setting(uid)
        if data.device_id is None:
            raise ValueError("XHome device id is unavailable")
        current_mask = int_value(data.first("notify_ctrl", "notifyCtrl")) or 0
        return self.client.set_notify_control(
            data.device_id,
            set_notify_category_enabled(current_mask, event_ids, enabled),
        )

    def _update_device_push_fields(
        self,
        data: XHomeDeviceRuntimeData,
        *,
        push: int | None = None,
        ispush: int | None = None,
    ) -> JSON:
        """Update app-level push fields while preserving the other flag."""

        if data.device_id is None:
            raise ValueError("XHome device id is unavailable")

        current_push = int_value(data.first("push"))
        current_ispush = int_value(data.first("ispush"))
        return self.client.update_device(
            data.device_id,
            name=data.name,
            push=current_push if push is None else push,
            ispush=current_ispush if ispush is None else ispush,
        )

    def _device_for_setting(self, uid: str) -> XHomeDeviceRuntimeData:
        """Return current device runtime data or fail a setting update."""

        if self.data is None or uid not in self.data.devices:
            raise ValueError("XHome device is unavailable")
        return self.data.devices[uid]

    async def _async_event_poll_tick(self, now: Any) -> None:
        """Handle a scheduled event polling tick."""

        await self.async_poll_events()

    def _local_push_worker(self, stop_event: Event) -> None:
        """Run the native push socket in a background thread."""

        backoff = 2
        while not stop_event.is_set():
            try:
                client = self._new_worker_client()
                push_client = XHomePushClient(
                    client.region.push_host,
                    user_id=client.user_id,
                    timeout=min(client.timeout, 30),
                    register_info=build_push_register_info(
                        client.user_id,
                        model="xhome-api",
                        brand="HomeAssistant",
                    ),
                )
                self._local_push_client = push_client
                self._local_push_status.update({"running": True, "last_error": None})
                for message in push_client.iter_messages(stop_event=stop_event):
                    if stop_event.is_set():
                        break
                    self._local_push_status.update(
                        {
                            "connected": True,
                            "frames": int(self._local_push_status["frames"]) + 1,
                            "last_frame_command": message.command,
                            "last_frame_kind": message.kind,
                            "last_frame_at": int(time.time()),
                        }
                    )
                    LOGGER.debug(
                        "XHome local push frame command=%s kind=%s payload_length=%s",
                        message.command,
                        message.kind,
                        len(message.payload),
                    )
                    self._handle_local_push_message(client, message)
                backoff = 2
            except XHomeAuthError as err:
                self._local_push_status.update({"connected": False, "last_error": str(err)})
                LOGGER.warning("XHome local push authentication failed: %s", err)
                stop_event.wait(60)
            except (XHomePushError, XHomeAPIError, XHomeError, OSError, TimeoutError, ValueError) as err:
                if not stop_event.is_set():
                    self._local_push_status.update(
                        {
                            "connected": False,
                            "reconnects": int(self._local_push_status["reconnects"]) + 1,
                            "last_error": str(err),
                        }
                    )
                    LOGGER.debug("XHome local push listener reconnecting after failure: %s", err)
                    stop_event.wait(backoff)
                    backoff = min(backoff * 2, 60)
            finally:
                if self._local_push_client is not None:
                    self._local_push_client.close()
                    self._local_push_client = None
                self._local_push_status["connected"] = False

    def _new_worker_client(self) -> XHomeClient:
        """Return a push worker client using the coordinator's active token."""

        self._ensure_login()

        return XHomeClient(
            region=_entry_region(self.config_entry),
            token=self.client.require_token(),
            user_id=self.client.require_user_id(),
            timeout=self.config_entry.options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
        )

    def _handle_local_push_message(self, client: XHomeClient, message: XHomePushMessage) -> None:
        """Handle one parsed native push message from the worker thread."""

        if message.kind == "token" and message.token:
            self._local_push_status["tokens"] = int(self._local_push_status["tokens"]) + 1
            self._register_local_push_token(client, message.token)
            return
        if message.kind == "event" and message.event:
            self._local_push_status.update(
                {
                    "events": int(self._local_push_status["events"]) + 1,
                    "last_event_at": int(time.time()),
                }
            )
            self.hass.add_job(self.async_handle_local_push_event, message.event)

    def _register_local_push_token(self, client: XHomeClient, push_token: str) -> None:
        """Register the socket token with XHome's push-token endpoints."""

        if push_token == self._local_push_registered_token:
            return
        client.register_push_tokens(
            push_token,
            push_platform="FCM",
            os_token="",
            language="en",
            os_name="ANDROID",
            os_push_version=1,
            phone_model="xhome-api",
        )
        self._local_push_registered_token = push_token
        self._local_push_status.update(
            {
                "registered": True,
                "registered_token_tail": f"...{push_token[-6:]}",
            }
        )
        LOGGER.info("Registered XHome local push token")

    def _update_data(self) -> XHomeCoordinatorData:
        """Synchronous data update helper."""

        self._ensure_login()
        try:
            payload = self.client.list_devices_resilient()
        except XHomeAPIError as err:
            if not _is_no_user_error(err):
                raise
            self.client.token = None
            self._ensure_login()
            payload = self.client.list_devices_resilient()
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

    def _prepare_live_stream(self, uid: str) -> XHomeLiveStreamSession:
        """Synchronous live-stream preparation helper."""

        data = self._device_for_setting(uid)
        self._ensure_login()
        token_payload = self.client.get_device_token(uid=uid)
        token = _live_token_from_payload(token_payload)
        if not token:
            if data.device_id is None:
                raise ValueError("XHome live token response did not include a token")
            token_payload = self.client.get_device_token(device_id=data.device_id)
            token = _live_token_from_payload(token_payload)
        if not token:
            raise ValueError("XHome live token response did not include a token")
        return XHomeLiveStreamSession(
            uid=uid,
            device_id=data.device_id,
            model=data.model,
            native_iot_host=normalize_region(_entry_region(self.config_entry)).native_iot_host,
            token=token,
            token_payload=token_payload,
        )

    def _poll_device_events(self) -> list[dict[str, Any]]:
        """Fetch recent event records for all known devices."""

        if self.data is None:
            return []
        return self._poll_device_events_for_uids(set(self.data.devices))

    def _poll_device_events_for_uids(self, uids: set[str]) -> list[dict[str, Any]]:
        """Fetch recent event records for selected devices."""

        self._ensure_login()
        if self.data is None:
            return []

        events: list[dict[str, Any]] = []
        for data in self.data.devices.values():
            if data.uid not in uids:
                continue
            device_type = string_value(data.first("type", "model", "device_type", "deviceType")) or "9"
            payload = self.client.get_new_device_events(data.uid, device_type)
            for record in event_records(payload):
                events.append(self._event_from_record(data.uid, data.device, record, source="poll"))
        return events

    def _event_from_record(
        self,
        uid: str,
        device: dict[str, Any],
        record: dict[str, Any],
        *,
        source: str,
    ) -> dict[str, Any]:
        """Build the coordinator's internal event wrapper."""

        key = event_key(uid, record)
        payload = {
            **event_payload(device, record),
            "event_key": key,
            "source": source,
        }
        return {
            "uid": uid,
            "event_key": key,
            "doorbell": is_doorbell_event(record),
            "has_image": event_has_image(record),
            "has_media": event_has_media(record),
            "record": record,
            "sort_key": _record_sort_key(record),
            "payload": payload,
            "bus_event_types": _event_bus_types(record),
        }

    def _fire_event_bus_events(self, event: dict[str, Any]) -> None:
        """Fire the generic and classified Home Assistant event bus events."""

        payload = event["payload"]
        self.hass.bus.async_fire(EVENT_XHOME_EVENT, payload)
        for event_type in event["bus_event_types"]:
            self.hass.bus.async_fire(event_type, payload)

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
                media_items = await self.hass.async_add_executor_job(self._resolve_event_media, event)
            except XHomeAuthError as err:
                self.client.token = None
                LOGGER.warning("XHome event media authentication failed: %s", err)
                continue
            except (XHomeAPIError, XHomeError, requests.RequestException, TimeoutError, ValueError) as err:
                LOGGER.debug("Skipping XHome event media for %s: %s", event.get("event_key"), err)
                continue
            for media in media_items:
                updated = self._cache_event_media(media) or updated
        return updated

    def _resolve_event_media(self, event: dict[str, Any]) -> list[XHomeLatestEventMedia]:
        """Resolve a signed media URL for an event."""

        self._ensure_login()

        uid = event["uid"]
        record = event["record"]
        event_guid = string_value(first_present(record, "event_guid", "eventGuid", "guid"))
        resolved: list[XHomeLatestEventMedia] = []
        url = media_url_from_event(record)

        if url is not None:
            resolved.append(self._media_from_url(event, url, media_item=None))

        if event_guid:
            media_payload = self.client.get_media_url(uid, event_guid)
            for media_item in media_items(media_payload):
                if item_url := media_url_from_item(media_item):
                    resolved.append(self._media_from_url(event, item_url, media_item=media_item))
        return resolved

    def _media_from_url(
        self,
        event: dict[str, Any],
        url: str,
        *,
        media_item: dict[str, Any] | None,
    ) -> XHomeLatestEventMedia:
        """Build media metadata for one resolved URL."""

        record = event["record"]
        file_name = string_value(media_item.get("file_name")) if media_item else None
        content_type = guess_media_content_type(url, file_name)
        return XHomeLatestEventMedia(
            uid=event["uid"],
            event_key=event["event_key"],
            event_guid=string_value(first_present(record, "event_guid", "eventGuid", "guid")),
            event_id=string_value(first_present(record, "id", "event_id", "eventId")),
            event_type=string_value(record.get("type")),
            time=string_value(record.get("time")),
            time_stamp=int_value(first_present(record, "time_stamp", "timeStamp", "timestamp")),
            url=url,
            media_kind=_media_kind(url, content_type=content_type, file_name=file_name),
            file_name=file_name,
            exp_time=int_value(media_item.get("exp_time")) if media_item else None,
            content_type=content_type,
            video_status=int_value(record.get("video_status")),
            video_size=int_value(record.get("video_size")),
        )

    def _cache_event_media(self, media: XHomeLatestEventMedia) -> bool:
        """Cache media by kind and return True when a cache changed."""

        if media.media_kind == "video":
            cache = self._latest_event_video_media
        elif media.media_kind == "image":
            cache = self._latest_event_media
        else:
            cache = self._latest_event_media

        current = cache.get(media.uid)
        if current is not None and _media_sort_key(current) > _media_sort_key(media):
            return False
        cache[media.uid] = media
        return True

    def _download_latest_event_image(self, uid: str) -> bytes | None:
        """Download latest event image bytes from the cached signed URL."""

        media = self._latest_event_media.get(uid)
        if media is None:
            return None

        content = self._download_event_media_bytes(media)
        if content is None:
            return None
        if media.media_kind == "video":
            return None
        if media.content_type is not None and not media.content_type.startswith("image/"):
            return None
        return content

    def _download_event_media_bytes(self, media: XHomeLatestEventMedia) -> bytes | None:
        """Download event media bytes from the cached signed URL."""

        if _media_url_expired(media):
            self._refresh_event_media_url(media)
        response = self.client.session.get(media.url, timeout=self.client.timeout)
        response.raise_for_status()
        response_content_type = _response_content_type(response)
        content_type = _usable_content_type(response_content_type) or media.content_type or response_content_type
        media.content_type = content_type
        media_kind = _media_kind(media.url, content_type=content_type, file_name=media.file_name)
        if media_kind != "unknown":
            media.media_kind = media_kind
        return response.content or None

    def _download_latest_event_media_files(self, uid: str) -> XHomeDownloadedEventMedia | None:
        """Download latest image/video media files into Home Assistant's media directory."""

        event_media = (
            self._latest_event_media.get(uid),
            self._latest_event_video_media.get(uid),
        )
        available_media = [media for media in event_media if media is not None]
        if not available_media:
            return None

        media_dir = Path(self.hass.config.path("media", DOMAIN, device_key(uid)))
        media_dir.mkdir(parents=True, exist_ok=True)

        image_path: str | None = None
        video_path: str | None = None
        media_count = 0
        for media in available_media:
            content = self._download_event_media_bytes(media)
            if not content:
                continue
            filename = _media_filename(media)
            path = media_dir / filename
            path.write_bytes(content)
            rel_path = str(Path("media", DOMAIN, device_key(uid), filename))
            media_count += 1
            if media.media_kind == "video":
                self._latest_event_video_media[uid] = media
                video_path = rel_path
            else:
                self._latest_event_media[uid] = media
                image_path = rel_path

        if media_count == 0:
            return None

        newest = max(available_media, key=_media_sort_key)
        return XHomeDownloadedEventMedia(
            uid=uid,
            event_key=newest.event_key,
            saved_at=int(time.time()),
            media_count=media_count,
            image_path=image_path,
            video_path=video_path,
        )

    def _refresh_event_media_url(self, media: XHomeLatestEventMedia) -> None:
        """Refresh an expired signed media URL when possible."""

        if not media.event_guid:
            return

        self._ensure_login()
        media_payload = self.client.get_media_url(media.uid, media.event_guid)
        media_item = _matching_media_item(media_payload, media.media_kind)
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
        media.media_kind = _media_kind(url, content_type=media.content_type, file_name=file_name)

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


def _noop() -> None:
    """Do nothing."""


def _entry_region(config_entry: ConfigEntry) -> str:
    """Return the active region, allowing options to override old entry data."""

    return config_entry.options.get(CONF_REGION) or config_entry.data.get(CONF_REGION, DEFAULT_REGION)


def _is_no_user_error(err: XHomeAPIError) -> bool:
    """Return whether the API rejected the current token as having no user."""

    payload = err.payload
    return err.status_code == 400 and isinstance(payload, dict) and payload.get("message") == "no user"


def _live_token_from_payload(payload: JSON | None) -> str | None:
    """Extract a live P2P token from known XHome live-token response shapes."""

    if not isinstance(payload, dict):
        return None
    if token := string_value(payload.get("token")):
        return token
    data = unwrap_dict(payload)
    return string_value(data.get("token"))


def _event_sort_key(event: dict[str, Any]) -> tuple[int, str]:
    """Return a stable event ordering key for coordinator event wrappers."""

    return event["sort_key"]


def _media_sort_key(media: XHomeLatestEventMedia) -> tuple[int, str]:
    """Return a stable ordering key for cached media."""

    return (media.time_stamp or 0, media.event_id or media.event_guid or media.event_key)


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


def _media_kind(url: str, *, content_type: str | None = None, file_name: str | None = None) -> str:
    """Return whether a media URL looks like an image, video, or unknown file."""

    usable_content_type = _usable_content_type(content_type)
    if is_image_media(url, content_type=usable_content_type, file_name=file_name):
        return "image"
    if is_video_media(url, content_type=usable_content_type, file_name=file_name):
        return "video"
    return "unknown"


def _matching_media_item(payload: JSON | None, media_kind: str) -> dict[str, Any] | None:
    """Return the best replacement OSS item for a cached media kind."""

    items = media_items(payload)
    for item in items:
        url = media_url_from_item(item)
        if url is None:
            continue
        file_name = string_value(item.get("file_name"))
        if media_kind == "image" and is_image_media(url, file_name=file_name):
            return item
        if media_kind == "video" and is_video_media(url, file_name=file_name):
            return item
    return items[0] if items else None


def _media_filename(media: XHomeLatestEventMedia) -> str:
    """Return a safe local filename for downloaded event media."""

    if media.file_name:
        file_name = Path(media.file_name.replace("\\", "/")).name
    else:
        file_name = f"{_event_file_stem(media)}{_media_extension(media)}"

    sanitized = MEDIA_FILENAME_SAFE.sub("_", file_name).strip("._-")
    if not sanitized:
        sanitized = f"{_event_file_stem(media)}{_media_extension(media)}"
    if "." not in sanitized:
        sanitized = f"{sanitized}{_media_extension(media)}"
    return sanitized


def _event_file_stem(media: XHomeLatestEventMedia) -> str:
    """Return a stable filename stem without leaking the full device UID."""

    key_tail = media.event_guid or media.event_id or media.event_key.rsplit(":", 1)[-1] or "event"
    stem = MEDIA_FILENAME_SAFE.sub("_", key_tail).strip("._-")
    return stem or "event"


def _media_extension(media: XHomeLatestEventMedia) -> str:
    """Return a file extension for downloaded media."""

    suffix = Path(urlparse(media.url).path).suffix
    if suffix:
        return suffix[:16]
    if media.content_type:
        extension = mimetypes.guess_extension(media.content_type)
        if extension:
            return extension
    if media.media_kind == "video":
        return ".mp4"
    if media.media_kind == "image":
        return ".jpg"
    return ".bin"


def _usable_content_type(content_type: str | None) -> str | None:
    """Ignore generic object-store content types when filenames are more useful."""

    if content_type is None:
        return None
    normalized = content_type.split(";", 1)[0].strip().lower()
    if normalized in {"application/octet-stream", "binary/octet-stream"}:
        return None
    return normalized


def _flag(value: bool) -> int:
    return 1 if value else 0
