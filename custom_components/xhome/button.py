"""Button platform for the XHome Home Assistant integration."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import XHomeDataUpdateCoordinator
from .entity import XHomeEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up XHome button entities."""

    coordinator: XHomeDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(XHomeFetchLatestEventMediaButton(coordinator, uid) for uid in coordinator.data.devices)


class XHomeFetchLatestEventMediaButton(XHomeEntity, ButtonEntity):
    """Fetch and save the latest XHome event media."""

    _attr_icon = "mdi:file-download-outline"
    _attr_translation_key = "fetch_latest_event_media"

    def __init__(self, coordinator: XHomeDataUpdateCoordinator, uid: str) -> None:
        """Initialize the media fetch button."""

        super().__init__(coordinator, uid, "fetch_latest_event_media")

    async def async_press(self) -> None:
        """Download the latest image/video event media to Home Assistant media."""

        await self.coordinator.async_download_latest_event_media(self.uid)
