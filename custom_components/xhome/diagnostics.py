"""Diagnostics for the XHome Home Assistant integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import CONF_REGION, DOMAIN
from .coordinator import XHomeDataUpdateCoordinator
from .helpers import redact_uid


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return redacted diagnostics for a config entry."""

    coordinator: XHomeDataUpdateCoordinator | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    devices: list[dict[str, Any]] = []
    if coordinator and coordinator.data:
        for data in coordinator.data.devices.values():
            devices.append(
                {
                    "name": data.name,
                    "uid_tail": redact_uid(data.uid),
                    "device_id": data.device_id,
                    "model": data.model,
                    "optional_payload_errors": data.errors,
                }
            )

    return {
        "entry": {
            "title": entry.title,
            "username": _redact_username(entry.data.get(CONF_USERNAME)),
            "password": "**REDACTED**" if entry.data.get(CONF_PASSWORD) else None,
            "region": entry.data.get(CONF_REGION),
            "active_region": entry.options.get(CONF_REGION) or entry.data.get(CONF_REGION),
            "options": dict(entry.options),
        },
        "devices": devices,
    }


def _redact_username(username: str | None) -> str | None:
    """Redact a username while keeping enough for troubleshooting."""

    if not username:
        return None
    if "@" in username:
        name, domain = username.split("@", 1)
        return f"{name[:2]}***@{domain}"
    return f"{username[:2]}***"
