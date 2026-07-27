"""Image platform for the XHome Home Assistant integration."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import XHomeDataUpdateCoordinator, XHomeLatestEventMedia
from .entity import XHomeEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up XHome image entities."""

    coordinator: XHomeDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(XHomeLatestEventImage(coordinator, uid) for uid in coordinator.data.devices)


class XHomeLatestEventImage(XHomeEntity, ImageEntity):
    """Latest event image for an XHome device."""

    _attr_should_poll = False
    _attr_translation_key = "latest_event_image"

    def __init__(self, coordinator: XHomeDataUpdateCoordinator, uid: str) -> None:
        """Initialize the latest event image entity."""

        super().__init__(coordinator, uid, "latest_event_image")
        ImageEntity.__init__(self, coordinator.hass)

    @property
    def available(self) -> bool:
        """Return whether a latest event image is available."""

        media = self._media
        return super().available and media is not None and (
            media.content_type is None or media.content_type.startswith("image/")
        )

    @property
    def content_type(self) -> str:
        """Return the latest image content type."""

        media = self._media
        return media.content_type if media and media.content_type else "image/jpeg"

    @property
    def image_last_updated(self) -> datetime | None:
        """Return when the latest image event was captured."""

        media = self._media
        if media is None or media.time_stamp is None:
            return None
        timestamp = media.time_stamp / 1000 if media.time_stamp > 10_000_000_000 else media.time_stamp
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return non-sensitive latest event image metadata."""

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
                "expires_at": media.exp_time,
                "video_status": media.video_status,
                "video_size": media.video_size,
            }
        )
        downloaded = self.coordinator.downloaded_event_media(self.uid)
        if downloaded is not None and downloaded.event_key == media.event_key:
            attrs.update(
                {
                    "saved_image_path": downloaded.image_path,
                    "saved_at": downloaded.saved_at,
                }
            )
        return {key: value for key, value in attrs.items() if value is not None}

    async def async_image(self) -> bytes | None:
        """Return latest event image bytes."""

        return await self.coordinator.async_get_latest_event_image(self.uid)

    @property
    def _media(self) -> XHomeLatestEventMedia | None:
        """Return cached media for this entity."""

        return self.coordinator.latest_event_media(self.uid)
