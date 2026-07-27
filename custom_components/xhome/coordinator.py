"""Data coordinator for the XHome Home Assistant integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
import logging
from typing import Any

import requests
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import XHomeAPIError, XHomeAuthError, XHomeClient, XHomeError
from .api.client import JSON
from .const import (
    CONF_REGION,
    CONF_SCAN_INTERVAL,
    CONF_TIMEOUT,
    DEFAULT_REGION,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TIMEOUT,
    DOMAIN,
)
from .helpers import device_name, device_uid, first_from_sources, int_value, string_value, unwrap_dict

LOGGER = logging.getLogger(__name__)


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
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )

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

    def _unlock_device(self, uid: str) -> JSON:
        """Synchronous unlock helper."""

        self._ensure_login()
        return self.client.unlock_door(uid)

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
