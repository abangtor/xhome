"""Button platform for the XHome Home Assistant integration."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import XHomeDataUpdateCoordinator, XHomeDeviceRuntimeData
from .entity import XHomeEntity
from .helpers import int_value

LOCK_DEVICE_TYPES = {9}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up XHome button entities."""

    coordinator: XHomeDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        XHomeUnlockButton(coordinator, uid)
        for uid, device in coordinator.data.devices.items()
        if _looks_like_lock(device)
    )


class XHomeUnlockButton(XHomeEntity, ButtonEntity):
    """One-shot unlock button for an XHome door."""

    _attr_icon = "mdi:lock-open-variant"
    _attr_translation_key = "unlock"

    def __init__(self, coordinator: XHomeDataUpdateCoordinator, uid: str) -> None:
        """Initialize the unlock button."""

        super().__init__(coordinator, uid, "unlock_button")

    async def async_press(self) -> None:
        """Unlock the door through the XHome cloud."""

        await self.coordinator.async_unlock_device(self.uid)
        await self.coordinator.async_request_refresh()


def _looks_like_lock(device: XHomeDeviceRuntimeData) -> bool:
    """Return True when the device should expose door action buttons."""

    model = int_value(device.model)
    if model in LOCK_DEVICE_TYPES:
        return True
    name = device.name.lower()
    return "door" in name or "lock" in name
