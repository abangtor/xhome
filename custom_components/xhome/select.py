"""Select platform for the XHome Home Assistant integration."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import XHomeDataUpdateCoordinator, XHomeDeviceRuntimeData
from .entity import XHomeEntity
from .helpers import int_value

STANDBY_OPTIONS = {
    "Normal standby": 0,
    "Trigger mode": 1,
}
STANDBY_OPTIONS_BY_VALUE = {value: option for option, value in STANDBY_OPTIONS.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up XHome select entities."""

    coordinator: XHomeDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        XHomeStandbyModeSelect(coordinator, uid)
        for uid, device in coordinator.data.devices.items()
        if _has_standby_mode(device)
    )


class XHomeStandbyModeSelect(XHomeEntity, SelectEntity):
    """XHome standby mode select."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:power-sleep"
    _attr_options = list(STANDBY_OPTIONS)
    _attr_translation_key = "standby_mode"

    def __init__(self, coordinator: XHomeDataUpdateCoordinator, uid: str) -> None:
        """Initialize the select entity."""

        super().__init__(coordinator, uid, "standby_mode")

    @property
    def current_option(self) -> str | None:
        """Return the current standby mode option."""

        if (data := self.device_data) is None:
            return None
        standby_mode = int_value(data.first("standby_mode", "standbyMode"))
        if standby_mode is None:
            return None
        return STANDBY_OPTIONS_BY_VALUE.get(standby_mode)

    async def async_select_option(self, option: str) -> None:
        """Set the standby mode."""

        await self.coordinator.async_set_standby_mode(self.uid, STANDBY_OPTIONS[option])


def _has_standby_mode(data: XHomeDeviceRuntimeData) -> bool:
    standby_mode = int_value(data.first("standby_mode", "standbyMode"))
    return standby_mode is not None and standby_mode >= 0
