"""Lock platform for the XHome Home Assistant integration."""

from __future__ import annotations

import time
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

OPTIMISTIC_UNLOCK_SECONDS = 8
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

    _attr_assumed_state = True
    _attr_translation_key = "door_lock"

    def __init__(self, coordinator: XHomeDataUpdateCoordinator, uid: str) -> None:
        """Initialize the lock entity."""

        super().__init__(coordinator, uid, "lock")
        self._optimistic_unlocked_until = 0.0

    @property
    def is_locked(self) -> bool | None:
        """Return lock state.

        XHome REST confirms unlock execution, but does not expose a reliable
        locked-state read yet. Treat the entity as assumed locked except for a
        short optimistic window after a successful unlock call.
        """

        return time.monotonic() >= self._optimistic_unlocked_until

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the door through the XHome cloud."""

        await self.coordinator.async_unlock_device(self.uid)
        self._optimistic_unlocked_until = time.monotonic() + OPTIMISTIC_UNLOCK_SECONDS
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_lock(self, **kwargs: Any) -> None:
        """Reject lock requests because no lock REST command is known."""

        raise HomeAssistantError("XHome REST API does not expose a lock command")


def _looks_like_lock(device: XHomeDeviceRuntimeData) -> bool:
    """Return True when the device should expose a lock entity."""

    model = int_value(device.model)
    if model in LOCK_DEVICE_TYPES:
        return True
    name = device.name.lower()
    return "door" in name or "lock" in name

