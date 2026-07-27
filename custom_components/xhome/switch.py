"""Switch platform for the XHome Home Assistant integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import XHomeDataUpdateCoordinator, XHomeDeviceRuntimeData
from .entity import XHomeEntity
from .helpers import bool_value, int_value, notify_category_enabled


@dataclass(frozen=True, kw_only=True)
class XHomeSwitchEntityDescription(SwitchEntityDescription):
    """Description for an XHome switch."""

    value_fn: Callable[[XHomeDeviceRuntimeData], bool | None]
    set_method: str
    exists_fn: Callable[[XHomeDeviceRuntimeData], bool] = lambda data: True
    event_ids: tuple[int, ...] = ()


SWITCHES: tuple[XHomeSwitchEntityDescription, ...] = (
    XHomeSwitchEntityDescription(
        key="push_notifications",
        translation_key="push_notifications",
        icon="mdi:bell",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data: bool_value(data.first("push")),
        set_method="async_set_push_enabled",
        exists_fn=lambda data: _has_flag(data, "push"),
    ),
    XHomeSwitchEntityDescription(
        key="offline_notifications",
        translation_key="offline_notifications",
        icon="mdi:bell-alert",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data: bool_value(data.first("ispush")),
        set_method="async_set_offline_notifications",
        exists_fn=lambda data: _has_flag(data, "ispush"),
    ),
    XHomeSwitchEntityDescription(
        key="activity_notifications",
        translation_key="activity_notifications",
        icon="mdi:motion-sensor",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data: _notify_enabled(data, (0,)),
        set_method="async_set_notification_category",
        event_ids=(0,),
        exists_fn=lambda data: _has_notify_mask(data),
    ),
    XHomeSwitchEntityDescription(
        key="doorbell_call_notifications",
        translation_key="doorbell_call_notifications",
        icon="mdi:doorbell-video",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data: _notify_enabled(data, (1,)),
        set_method="async_set_notification_category",
        event_ids=(1,),
        exists_fn=lambda data: _has_notify_mask(data),
    ),
    XHomeSwitchEntityDescription(
        key="lock_event_notifications",
        translation_key="lock_event_notifications",
        icon="mdi:lock-clock",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data: _notify_enabled(data, (5, 6)),
        set_method="async_set_notification_category",
        event_ids=(5, 6),
        exists_fn=lambda data: _has_notify_mask(data),
    ),
    XHomeSwitchEntityDescription(
        key="battery_display",
        translation_key="battery_display",
        icon="mdi:battery",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data: bool_value(data.first("bat_display_en", "battery_display", "batteryDisplay")),
        set_method="async_set_battery_display",
        exists_fn=lambda data: _has_flag(data, "bat_display_en", "battery_display", "batteryDisplay"),
    ),
    XHomeSwitchEntityDescription(
        key="weather_forecast",
        translation_key="weather_forecast",
        icon="mdi:weather-partly-cloudy",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data: bool_value(data.first("wet_play", "wetPlay")),
        set_method="async_set_wet_play",
        exists_fn=lambda data: _has_flag(data, "wet_play", "wetPlay"),
    ),
    XHomeSwitchEntityDescription(
        key="call_screen",
        translation_key="call_screen",
        icon="mdi:cellphone-screenshot",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data: bool_value(data.first("call_screen_on", "call_screen", "callScreen")),
        set_method="async_set_call_screen",
        exists_fn=lambda data: _has_flag(data, "call_screen_on", "call_screen", "callScreen"),
    ),
    XHomeSwitchEntityDescription(
        key="remote_unlock_anytime",
        translation_key="remote_unlock_anytime",
        icon="mdi:lock-open-check",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data: _remote_unlock_anytime(data),
        set_method="async_set_remote_unlock_anytime",
        exists_fn=lambda data: _has_flag(data, "unlock_limit", "unlockLimit"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up XHome switch entities."""

    coordinator: XHomeDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        XHomeSwitch(coordinator, uid, description)
        for uid, device in coordinator.data.devices.items()
        for description in SWITCHES
        if description.exists_fn(device)
    )


class XHomeSwitch(XHomeEntity, SwitchEntity):
    """XHome writable switch entity."""

    entity_description: XHomeSwitchEntityDescription

    def __init__(
        self,
        coordinator: XHomeDataUpdateCoordinator,
        uid: str,
        description: XHomeSwitchEntityDescription,
    ) -> None:
        """Initialize the switch."""

        super().__init__(coordinator, uid, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return the switch state."""

        if (data := self.device_data) is None:
            return None
        return self.entity_description.value_fn(data)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the setting on."""

        await self._async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the setting off."""

        await self._async_set_enabled(False)

    async def _async_set_enabled(self, enabled: bool) -> None:
        method = getattr(self.coordinator, self.entity_description.set_method)
        if self.entity_description.event_ids:
            await method(self.uid, self.entity_description.event_ids, enabled)
        else:
            await method(self.uid, enabled)


def _has_flag(data: XHomeDeviceRuntimeData, *keys: str) -> bool:
    value = int_value(data.first(*keys))
    return value is not None and value >= 0


def _has_notify_mask(data: XHomeDeviceRuntimeData) -> bool:
    return data.device_id is not None and int_value(data.first("notify_ctrl", "notifyCtrl")) is not None


def _notify_enabled(data: XHomeDeviceRuntimeData, event_ids: tuple[int, ...]) -> bool | None:
    mask = int_value(data.first("notify_ctrl", "notifyCtrl"))
    if mask is None:
        return None
    return notify_category_enabled(mask, event_ids)


def _remote_unlock_anytime(data: XHomeDeviceRuntimeData) -> bool | None:
    unlock_limit = int_value(data.first("unlock_limit", "unlockLimit"))
    if unlock_limit is None or unlock_limit < 0:
        return None
    return unlock_limit == 0
