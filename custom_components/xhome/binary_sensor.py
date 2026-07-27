"""Binary sensor platform for the XHome Home Assistant integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import XHomeDataUpdateCoordinator, XHomeDeviceRuntimeData
from .entity import XHomeEntity
from .helpers import bool_value, int_value


@dataclass(frozen=True, kw_only=True)
class XHomeBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Description for an XHome binary sensor."""

    value_fn: Callable[[XHomeDeviceRuntimeData], bool | None]


BINARY_SENSORS: tuple[XHomeBinarySensorEntityDescription, ...] = (
    XHomeBinarySensorEntityDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda data: _online_value(data),
    ),
    XHomeBinarySensorEntityDescription(
        key="battery_display",
        translation_key="battery_display",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: bool_value(data.first("bat_display_en", "battery_display", "batteryDisplay")),
    ),
    XHomeBinarySensorEntityDescription(
        key="call_screen",
        translation_key="call_screen",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: bool_value(data.first("call_screen_on", "call_screen", "callScreen")),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up XHome binary sensor entities."""

    coordinator: XHomeDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        XHomeBinarySensor(coordinator, uid, description)
        for uid in coordinator.data.devices
        for description in BINARY_SENSORS
    )


class XHomeBinarySensor(XHomeEntity, BinarySensorEntity):
    """XHome binary sensor entity."""

    entity_description: XHomeBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: XHomeDataUpdateCoordinator,
        uid: str,
        description: XHomeBinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary sensor."""

        super().__init__(coordinator, uid, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return the binary sensor state."""

        if (data := self.device_data) is None:
            return None
        return self.entity_description.value_fn(data)


def _online_value(data: XHomeDeviceRuntimeData) -> bool | None:
    """Return best-effort online status from cloud fields."""

    explicit = bool_value(data.first("online", "is_online", "isOnline"))
    if explicit is not None:
        return explicit

    online_type = int_value(data.first("online_type", "onlineType"))
    if online_type is not None:
        return online_type > 0

    status = int_value(data.first("status"))
    if status is not None:
        return status == 0

    return None
