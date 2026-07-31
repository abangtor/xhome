"""Binary sensor platform for the XHome Home Assistant integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
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
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up XHome binary sensor entities."""

    coordinator: XHomeDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            *(
                XHomeBinarySensor(coordinator, uid, description)
                for uid in coordinator.data.devices
                for description in BINARY_SENSORS
            ),
            *(XHomeLockedBinarySensor(coordinator, uid) for uid in coordinator.data.devices),
        ]
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


class XHomeLockedBinarySensor(XHomeEntity, BinarySensorEntity):
    """Derived lock-state binary sensor."""

    _attr_translation_key = "locked"

    def __init__(self, coordinator: XHomeDataUpdateCoordinator, uid: str) -> None:
        """Initialize the derived locked sensor."""

        super().__init__(coordinator, uid, "locked")

    @property
    def is_on(self) -> bool | None:
        """Return whether the latest lock/unlock event says the door is locked."""

        if self.device_data is None:
            return None
        return self.coordinator.lock_state(self.uid)

    @property
    def icon(self) -> str:
        """Return an icon matching the derived lock state."""

        locked = self.is_on
        if locked is None:
            return "mdi:lock-question"
        return "mdi:lock" if locked else "mdi:lock-open-variant"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return metadata for the event that last changed derived lock state."""

        attrs = super().extra_state_attributes
        latest = self.coordinator.latest_lock_state_event(self.uid)
        if latest is None:
            return attrs

        payload = latest.payload
        locked = self.is_on
        attrs.update(
            {
                "lock_state": "locked" if locked else "unlocked" if locked is False else None,
                "lock_state_source": payload.get("source"),
                "lock_state_event_kind": payload.get("event_kind"),
                "lock_event_type": payload.get("lock_event_type"),
                "lock_event_type_name": payload.get("lock_event_type_name"),
                "lock_event_content": payload.get("lock_event_content"),
                "lock_event_content_name": payload.get("lock_event_content_name"),
                "event_key": payload.get("event_key"),
                "event_guid": payload.get("event_guid"),
                "event_id": payload.get("event_id"),
                "event_type": payload.get("event_type"),
                "event_type_name": payload.get("event_type_name"),
                "event_time": payload.get("time"),
                "event_time_stamp": payload.get("time_stamp"),
            }
        )
        return {key: value for key, value in attrs.items() if value is not None}


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
