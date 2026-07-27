"""Base entities for the XHome Home Assistant integration."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import XHomeDataUpdateCoordinator, XHomeDeviceRuntimeData
from .helpers import device_key, redact_uid, string_value


class XHomeEntity(CoordinatorEntity[XHomeDataUpdateCoordinator]):
    """Base class for XHome entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: XHomeDataUpdateCoordinator, uid: str, suffix: str) -> None:
        """Initialize the entity."""

        super().__init__(coordinator)
        self.uid = uid
        self._device_key = device_key(uid)
        self._attr_unique_id = f"{self._device_key}_{suffix}"

    @property
    def device_data(self) -> XHomeDeviceRuntimeData | None:
        """Return the latest device data."""

        if self.coordinator.data is None:
            return None
        return self.coordinator.data.devices.get(self.uid)

    @property
    def available(self) -> bool:
        """Return whether the entity is available."""

        return super().available and self.device_data is not None

    @property
    def device_info(self) -> DeviceInfo:
        """Return Home Assistant device registry information."""

        data = self.device_data
        name = data.name if data else "XHome device"
        model = data.model if data else None
        firmware = _firmware_version(data) if data else None
        return {
            "identifiers": {(DOMAIN, self._device_key)},
            "manufacturer": "XHome/Lancens",
            "name": name,
            "model": model,
            "sw_version": firmware,
        }

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return non-sensitive diagnostic attributes."""

        data = self.device_data
        if data is None:
            return {}
        attrs: dict[str, Any] = {
            "uid_tail": redact_uid(self.uid),
        }
        if data.device_id is not None:
            attrs["device_id"] = data.device_id
        if data.model is not None:
            attrs["xhome_model"] = data.model
        return attrs


def _firmware_version(data: XHomeDeviceRuntimeData | None) -> str | None:
    if data is None:
        return None
    return string_value(data.first("version", "firmware", "firmware_version", "current_version", "currentVersion"))

