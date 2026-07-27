"""Sensor platform for the XHome Home Assistant integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import XHomeDataUpdateCoordinator, XHomeDeviceRuntimeData, XHomeLatestEventMedia
from .entity import XHomeEntity
from .helpers import int_value, string_value

SIGNAL_STRENGTH_DBM = "dBm"


@dataclass(frozen=True, kw_only=True)
class XHomeSensorEntityDescription(SensorEntityDescription):
    """Description for an XHome sensor."""

    value_fn: Callable[[XHomeDeviceRuntimeData], Any]


SENSORS: tuple[XHomeSensorEntityDescription, ...] = (
    XHomeSensorEntityDescription(
        key="battery",
        translation_key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: int_value(data.first("battery", "bat", "battery_power", "electricity")),
    ),
    XHomeSensorEntityDescription(
        key="rssi",
        translation_key="rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DBM,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: int_value(data.first("rssi", "RSSI", "wifi_rssi", "wifiRssi")),
    ),
    XHomeSensorEntityDescription(
        key="online_type",
        translation_key="online_type",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: int_value(data.first("online_type", "onlineType")),
    ),
    XHomeSensorEntityDescription(
        key="firmware",
        translation_key="firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: string_value(
            data.first("version", "firmware", "firmware_version", "current_version", "currentVersion")
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up XHome sensor entities."""

    coordinator: XHomeDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            *(
                XHomeSensor(coordinator, uid, description)
                for uid in coordinator.data.devices
                for description in SENSORS
            ),
            *(XHomeLastEventSensor(coordinator, uid) for uid in coordinator.data.devices),
            *(XHomeLatestEventVideoSensor(coordinator, uid) for uid in coordinator.data.devices),
        ]
    )


class XHomeSensor(XHomeEntity, SensorEntity):
    """XHome sensor entity."""

    entity_description: XHomeSensorEntityDescription

    def __init__(
        self,
        coordinator: XHomeDataUpdateCoordinator,
        uid: str,
        description: XHomeSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""

        super().__init__(coordinator, uid, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""

        if (data := self.device_data) is None:
            return None
        return self.entity_description.value_fn(data)


class XHomeLastEventSensor(XHomeEntity, SensorEntity):
    """Latest XHome event sensor."""

    _attr_icon = "mdi:history"
    _attr_translation_key = "last_event"

    def __init__(self, coordinator: XHomeDataUpdateCoordinator, uid: str) -> None:
        """Initialize the latest event sensor."""

        super().__init__(coordinator, uid, "last_event")

    @property
    def native_value(self) -> str | None:
        """Return the latest event kind."""

        latest = self.coordinator.latest_event(self.uid)
        if latest is None:
            return None
        return latest.payload.get("event_kind")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return latest event metadata."""

        attrs = super().extra_state_attributes
        latest = self.coordinator.latest_event(self.uid)
        if latest is None:
            return attrs

        attrs.update(latest.payload)
        return {key: value for key, value in attrs.items() if value is not None}


class XHomeLatestEventVideoSensor(XHomeEntity, SensorEntity):
    """Latest XHome event video metadata sensor."""

    _attr_icon = "mdi:video"
    _attr_translation_key = "latest_event_video"

    def __init__(self, coordinator: XHomeDataUpdateCoordinator, uid: str) -> None:
        """Initialize the latest event video sensor."""

        super().__init__(coordinator, uid, "latest_event_video")

    @property
    def native_value(self) -> str | None:
        """Return whether the latest event has a video clip."""

        return "available" if self._media is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return non-sensitive latest event video metadata."""

        attrs = super().extra_state_attributes
        media = self._media
        if media is None:
            return attrs

        attrs.update(
            {
                "event_key": media.event_key,
                "event_guid": media.event_guid,
                "event_id": media.event_id,
                "event_type": media.event_type,
                "event_time": media.time,
                "event_time_stamp": media.time_stamp,
                "file_name": media.file_name,
                "content_type": media.content_type,
                "expires_at": media.exp_time,
                "video_status": media.video_status,
                "video_size": media.video_size,
            }
        )
        downloaded = self.coordinator.downloaded_event_media(self.uid)
        if downloaded is not None and downloaded.event_key == media.event_key:
            attrs.update(
                {
                    "saved_video_path": downloaded.video_path,
                    "saved_at": downloaded.saved_at,
                }
            )
        return {key: value for key, value in attrs.items() if value is not None}

    @property
    def _media(self) -> XHomeLatestEventMedia | None:
        """Return cached video media for this entity."""

        return self.coordinator.latest_event_video_media(self.uid)
