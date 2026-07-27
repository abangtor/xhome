"""Home Assistant custom integration for XHome/Lancens devices."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall

from .const import DOMAIN, PLATFORMS, SERVICE_REFRESH
from .coordinator import XHomeDataUpdateCoordinator

XHomeConfigEntry = ConfigEntry


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up integration-level services."""

    hass.data.setdefault(DOMAIN, {})

    async def handle_refresh(call: ServiceCall) -> None:
        """Refresh all configured XHome coordinators."""

        for coordinator in list(hass.data[DOMAIN].values()):
            await coordinator.async_request_refresh()

    hass.services.async_register(DOMAIN, SERVICE_REFRESH, handle_refresh)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: XHomeConfigEntry) -> bool:
    """Set up XHome from a config entry."""

    coordinator = XHomeDataUpdateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: XHomeConfigEntry) -> bool:
    """Unload a config entry."""

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: XHomeConfigEntry) -> None:
    """Handle options updates."""

    await hass.config_entries.async_reload(entry.entry_id)
