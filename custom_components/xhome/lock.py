"""Lock platform for the XHome Home Assistant integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
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
    """Set up XHome lock entities."""

    coordinator: XHomeDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        XHomeLockEntity(coordinator, uid)
        for uid, device in coordinator.data.devices.items()
        if _looks_like_lock(device)
    )


class XHomeLockEntity(XHomeEntity, LockEntity):
    """XHome door lock entity."""

    _attr_translation_key = "door_lock"

    def __init__(self, coordinator: XHomeDataUpdateCoordinator, uid: str) -> None:
        """Initialize the lock entity."""

        super().__init__(coordinator, uid, "lock")

    @property
    def is_locked(self) -> bool | None:
        """Return lock state.

        XHome REST confirms unlock execution, but does not expose a reliable
        locked-state read. Keep the entity locked so Home Assistant continues
        to offer the known-good unlock action instead of a cloud lock action.
        """

        return True

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the door through the XHome cloud."""

        await self.coordinator.async_unlock_device(self.uid)
        await self.coordinator.async_request_refresh()

    async def async_lock(self, **kwargs: Any) -> None:
        """Reject cloud lock attempts.

        The only recovered REST endpoint that looked like cloud locking caused
        logout/session side effects during live testing. Do not call it from HA.
        """

        raise HomeAssistantError("XHome cloud lock is not supported; unlock only is implemented")


def _looks_like_lock(device: XHomeDeviceRuntimeData) -> bool:
    """Return True when the device should expose a lock entity."""

    model = int_value(device.model)
    if model in LOCK_DEVICE_TYPES:
        return True
    name = device.name.lower()
    return "door" in name or "lock" in name
