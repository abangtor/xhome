"""Number platform for the XHome Home Assistant integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import XHomeDataUpdateCoordinator, XHomeDeviceRuntimeData
from .entity import XHomeEntity
from .helpers import int_value


@dataclass(frozen=True, kw_only=True)
class XHomeNumberEntityDescription(NumberEntityDescription):
    """Description for an XHome number entity."""

    value_fn: Callable[[XHomeDeviceRuntimeData], int | None]
    set_method: str
    exists_fn: Callable[[XHomeDeviceRuntimeData], bool] = lambda data: True
    min_fn: Callable[[XHomeDeviceRuntimeData], int | None] | None = None
    max_fn: Callable[[XHomeDeviceRuntimeData], int | None] | None = None


NUMBERS: tuple[XHomeNumberEntityDescription, ...] = (
    XHomeNumberEntityDescription(
        key="screen_timeout",
        translation_key="screen_timeout",
        icon="mdi:timer-outline",
        entity_category=EntityCategory.CONFIG,
        native_min_value=5,
        native_max_value=60,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value_fn=lambda data: int_value(data.first("screenon_timeout", "screen_on_timeout", "screenTimeout")),
        set_method="async_set_screen_timeout",
        exists_fn=lambda data: _has_screen_timeout(data),
    ),
    XHomeNumberEntityDescription(
        key="night_vision_target_ev",
        translation_key="night_vision_target_ev",
        icon="mdi:brightness-6",
        entity_category=EntityCategory.CONFIG,
        native_min_value=0,
        native_max_value=255,
        native_step=1,
        value_fn=lambda data: int_value(data.first("target_ev", "targetEv")),
        set_method="async_set_target_ev",
        exists_fn=lambda data: _has_target_ev(data),
        min_fn=lambda data: _ev_bound(data, "min_ev", "minEv"),
        max_fn=lambda data: _ev_bound(data, "max_ev", "maxEv"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up XHome number entities."""

    coordinator: XHomeDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        XHomeNumber(coordinator, uid, description)
        for uid, device in coordinator.data.devices.items()
        for description in NUMBERS
        if description.exists_fn(device)
    )


class XHomeNumber(XHomeEntity, NumberEntity):
    """XHome writable number entity."""

    entity_description: XHomeNumberEntityDescription

    def __init__(
        self,
        coordinator: XHomeDataUpdateCoordinator,
        uid: str,
        description: XHomeNumberEntityDescription,
    ) -> None:
        """Initialize the number entity."""

        super().__init__(coordinator, uid, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> int | None:
        """Return the number value."""

        if (data := self.device_data) is None:
            return None
        return self.entity_description.value_fn(data)

    @property
    def native_min_value(self) -> float:
        """Return the dynamic minimum value when the device reports one."""

        if self.entity_description.min_fn is not None and (data := self.device_data) is not None:
            value = self.entity_description.min_fn(data)
            if value is not None:
                return value
        return self.entity_description.native_min_value

    @property
    def native_max_value(self) -> float:
        """Return the dynamic maximum value when the device reports one."""

        if self.entity_description.max_fn is not None and (data := self.device_data) is not None:
            value = self.entity_description.max_fn(data)
            if value is not None:
                return value
        return self.entity_description.native_max_value

    async def async_set_native_value(self, value: float) -> None:
        """Set the numeric setting."""

        method = getattr(self.coordinator, self.entity_description.set_method)
        await method(self.uid, int(value))


def _has_target_ev(data: XHomeDeviceRuntimeData) -> bool:
    target = int_value(data.first("target_ev", "targetEv"))
    minimum = _ev_bound(data, "min_ev", "minEv")
    maximum = _ev_bound(data, "max_ev", "maxEv")
    return target is not None and target >= 0 and minimum is not None and maximum is not None


def _has_screen_timeout(data: XHomeDeviceRuntimeData) -> bool:
    return int_value(data.first("screenon_timeout", "screen_on_timeout", "screenTimeout")) is not None


def _ev_bound(data: XHomeDeviceRuntimeData, *keys: str) -> int | None:
    value = int_value(data.first(*keys))
    return value if value is not None and value >= 0 else None
