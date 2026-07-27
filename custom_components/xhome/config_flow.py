"""Config flow for the XHome Home Assistant integration."""

from __future__ import annotations

from typing import Any

import requests
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .api import XHomeAPIError, XHomeAuthError, XHomeClient, XHomeError
from .const import (
    CONF_EVENT_SCAN_INTERVAL,
    CONF_REGION,
    CONF_SCAN_INTERVAL,
    CONF_TIMEOUT,
    DEFAULT_EVENT_SCAN_INTERVAL,
    DEFAULT_REGION,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TIMEOUT,
    DOMAIN,
    REGIONS,
)


class XHomeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle an XHome config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> XHomeOptionsFlow:
        """Create the options flow."""

        return XHomeOptionsFlow(config_entry)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial setup step."""

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                info = await self.hass.async_add_executor_job(_validate_login, user_input)
            except XHomeAuthError:
                errors["base"] = "invalid_auth"
            except (XHomeAPIError, XHomeError, requests.RequestException, TimeoutError, ValueError):
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                errors["base"] = "unknown"
            else:
                unique_id = f"{user_input[CONF_REGION]}:{info['user_id'] or user_input[CONF_USERNAME].lower()}"
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=str(user_input[CONF_USERNAME]),
                    data={
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_REGION: user_input[CONF_REGION],
                    },
                    options={
                        CONF_SCAN_INTERVAL: user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                        CONF_EVENT_SCAN_INTERVAL: user_input.get(
                            CONF_EVENT_SCAN_INTERVAL, DEFAULT_EVENT_SCAN_INTERVAL
                        ),
                        CONF_TIMEOUT: user_input.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(user_input),
            errors=errors,
        )


class XHomeOptionsFlow(config_entries.OptionsFlow):
    """Handle XHome options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize the options flow."""

        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manage integration options."""

        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): vol.All(vol.Coerce(int), vol.Range(min=15, max=3600)),
                    vol.Required(
                        CONF_EVENT_SCAN_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_EVENT_SCAN_INTERVAL, DEFAULT_EVENT_SCAN_INTERVAL
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=30, max=3600)),
                    vol.Required(
                        CONF_TIMEOUT,
                        default=self.config_entry.options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
                    ): vol.All(vol.Coerce(int), vol.Range(min=5, max=120)),
                }
            ),
        )


def _user_schema(user_input: dict[str, Any] | None) -> vol.Schema:
    """Return the setup form schema."""

    user_input = user_input or {}
    return vol.Schema(
        {
            vol.Required(CONF_USERNAME, default=user_input.get(CONF_USERNAME, "")): str,
            vol.Required(CONF_PASSWORD): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Required(CONF_REGION, default=user_input.get(CONF_REGION, DEFAULT_REGION)): selector.SelectSelector(
                selector.SelectSelectorConfig(options=REGIONS)
            ),
            vol.Required(
                CONF_SCAN_INTERVAL,
                default=user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): vol.All(vol.Coerce(int), vol.Range(min=15, max=3600)),
            vol.Required(
                CONF_EVENT_SCAN_INTERVAL,
                default=user_input.get(CONF_EVENT_SCAN_INTERVAL, DEFAULT_EVENT_SCAN_INTERVAL),
            ): vol.All(vol.Coerce(int), vol.Range(min=30, max=3600)),
            vol.Required(CONF_TIMEOUT, default=user_input.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)): vol.All(
                vol.Coerce(int), vol.Range(min=5, max=120)
            ),
        }
    )


def _validate_login(user_input: dict[str, Any]) -> dict[str, Any]:
    """Validate credentials with the XHome cloud."""

    client = XHomeClient(
        region=user_input[CONF_REGION],
        timeout=user_input.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
    )
    session = client.login(user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
    devices = client.flatten_devices()
    return {
        "user_id": session.user_id,
        "device_count": len(devices),
    }
