"""Config flow for the XHome Home Assistant integration."""

from __future__ import annotations

import re
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
    CONF_IMAGE_ROTATION,
    CONF_LOCAL_PUSH_ENABLED,
    CONF_LOCK_USER_MAPPINGS,
    CONF_REGION,
    CONF_SCAN_INTERVAL,
    CONF_TIMEOUT,
    DEFAULT_EVENT_SCAN_INTERVAL,
    DEFAULT_IMAGE_ROTATION,
    DEFAULT_LOCAL_PUSH_ENABLED,
    DEFAULT_REGION,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TIMEOUT,
    DOMAIN,
    IMAGE_ROTATIONS,
    REGIONS,
)

CONF_DEVICE_UID = "device_uid"
CONF_IDS = "ids"
CONF_LOCK_USER_NAME = "lock_user_name"
CONF_NAME = "name"
CONF_PERSON = "person"
CONF_REMOVE_NAME = "remove_name"


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
                        CONF_IMAGE_ROTATION: user_input.get(CONF_IMAGE_ROTATION, DEFAULT_IMAGE_ROTATION),
                        CONF_LOCAL_PUSH_ENABLED: user_input.get(CONF_LOCAL_PUSH_ENABLED, DEFAULT_LOCAL_PUSH_ENABLED),
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

        self._config_entry = config_entry
        self._selected_lock_uid: str | None = None
        self._editing_lock_user_name: str | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manage integration options."""

        return self.async_show_menu(
            step_id="init",
            menu_options=["general", "add_lock_user", "edit_lock_user", "remove_lock_user"],
        )

    async def async_step_general(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manage general integration options."""

        if user_input is not None:
            return self.async_create_entry(title="", data=self._options_with(user_input))

        return self.async_show_form(
            step_id="general",
            data_schema=_general_options_schema(self._config_entry),
        )

    async def async_step_add_lock_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Choose the lock device for a user mapping."""

        if user_input is not None:
            self._selected_lock_uid = user_input[CONF_DEVICE_UID]
            self._editing_lock_user_name = None
            return await self.async_step_lock_user_mapping()

        return self.async_show_form(
            step_id="add_lock_user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_UID): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=self._device_options())
                    )
                }
            ),
        )

    async def async_step_lock_user_mapping(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Add or edit one lock user mapping."""

        if self._selected_lock_uid is None:
            return await self.async_step_add_lock_user()

        errors: dict[str, str] = {}
        existing = self._mapping_for_name(self._selected_lock_uid, self._editing_lock_user_name)
        if user_input is not None:
            ids = _parse_id_list(user_input.get(CONF_IDS))
            name = _clean_string(user_input.get(CONF_NAME))
            if not name:
                errors[CONF_NAME] = "required"
            if not ids:
                errors[CONF_IDS] = "required"
            if not errors:
                mappings = _copy_lock_user_mappings(self._config_entry.options)
                device_mappings = [
                    mapping
                    for mapping in mappings.get(self._selected_lock_uid, [])
                    if _clean_string(mapping.get("name")) not in {self._editing_lock_user_name, name}
                ]
                mapping: dict[str, Any] = {"name": name, "ids": ids}
                if person := _clean_string(user_input.get(CONF_PERSON)):
                    mapping["person"] = person
                device_mappings.append(mapping)
                mappings[self._selected_lock_uid] = device_mappings
                return self.async_create_entry(
                    title="",
                    data=self._options_with({CONF_LOCK_USER_MAPPINGS: mappings}),
                )

        return self.async_show_form(
            step_id="lock_user_mapping",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default=_clean_string(existing.get("name"))): str,
                    vol.Optional(CONF_PERSON, default=_clean_string(existing.get("person"))): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="person")
                    ),
                    vol.Required(CONF_IDS, default=_id_list_string(existing.get("ids"))): selector.TextSelector(
                        selector.TextSelectorConfig(multiline=True)
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                "device": self._device_label(self._selected_lock_uid),
                "current_mappings": self._mapping_summary(self._selected_lock_uid),
                "recent_unknown_ids": self._recent_unknown_id_summary(self._selected_lock_uid),
            },
        )

    async def async_step_edit_lock_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Choose the lock device for editing a user mapping."""

        if user_input is not None:
            self._selected_lock_uid = user_input[CONF_DEVICE_UID]
            return await self.async_step_choose_lock_user_mapping()

        return self.async_show_form(
            step_id="edit_lock_user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_UID): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=self._mapped_device_options())
                    )
                }
            ),
        )

    async def async_step_choose_lock_user_mapping(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Choose one configured lock user mapping to edit."""

        if self._selected_lock_uid is None:
            return await self.async_step_edit_lock_user()

        mapping_names = self._mapping_names(self._selected_lock_uid)
        if not mapping_names:
            return self.async_create_entry(title="", data=dict(self._config_entry.options))

        if user_input is not None:
            self._editing_lock_user_name = user_input[CONF_LOCK_USER_NAME]
            return await self.async_step_lock_user_mapping()

        return self.async_show_form(
            step_id="choose_lock_user_mapping",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LOCK_USER_NAME): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=mapping_names)
                    )
                }
            ),
            description_placeholders={
                "device": self._device_label(self._selected_lock_uid),
                "current_mappings": self._mapping_summary(self._selected_lock_uid),
            },
        )

    async def async_step_remove_lock_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Choose the lock device for removing a user mapping."""

        if user_input is not None:
            self._selected_lock_uid = user_input[CONF_DEVICE_UID]
            return await self.async_step_remove_lock_user_mapping()

        return self.async_show_form(
            step_id="remove_lock_user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_UID): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=self._mapped_device_options())
                    )
                }
            ),
        )

    async def async_step_remove_lock_user_mapping(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Remove one configured lock user mapping."""

        if self._selected_lock_uid is None:
            return await self.async_step_remove_lock_user()

        mapping_names = self._mapping_names(self._selected_lock_uid)
        if not mapping_names:
            return self.async_create_entry(title="", data=dict(self._config_entry.options))

        if user_input is not None:
            remove_name = user_input[CONF_REMOVE_NAME]
            mappings = _copy_lock_user_mappings(self._config_entry.options)
            remaining = [
                mapping
                for mapping in mappings.get(self._selected_lock_uid, [])
                if _clean_string(mapping.get("name")) != remove_name
            ]
            if remaining:
                mappings[self._selected_lock_uid] = remaining
            else:
                mappings.pop(self._selected_lock_uid, None)
            return self.async_create_entry(title="", data=self._options_with({CONF_LOCK_USER_MAPPINGS: mappings}))

        return self.async_show_form(
            step_id="remove_lock_user_mapping",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_REMOVE_NAME): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=mapping_names)
                    )
                }
            ),
            description_placeholders={
                "device": self._device_label(self._selected_lock_uid),
                "current_mappings": self._mapping_summary(self._selected_lock_uid),
            },
        )

    def _options_with(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Return existing options with updates applied."""

        options = dict(self._config_entry.options)
        options.update(updates)
        return options

    def _coordinator(self) -> Any | None:
        """Return the loaded coordinator for this entry when available."""

        return self.hass.data.get(DOMAIN, {}).get(self._config_entry.entry_id)

    def _device_options(self) -> list[dict[str, str]]:
        """Return selectable XHome lock devices."""

        options: list[dict[str, str]] = []
        coordinator = self._coordinator()
        if coordinator is not None and coordinator.data is not None:
            for uid, data in sorted(coordinator.data.devices.items(), key=lambda item: item[1].name):
                options.append({"value": uid, "label": data.name})
        for uid in _copy_lock_user_mappings(self._config_entry.options):
            if uid not in {option["value"] for option in options}:
                options.append({"value": uid, "label": uid})
        return options

    def _mapped_device_options(self) -> list[dict[str, str]]:
        """Return devices that have configured lock user mappings."""

        return [
            {"value": uid, "label": self._device_label(uid)}
            for uid, mappings in _copy_lock_user_mappings(self._config_entry.options).items()
            if mappings
        ]

    def _device_label(self, uid: str | None) -> str:
        """Return the current friendly label for a device UID."""

        if uid is None:
            return ""
        for option in self._device_options():
            if option["value"] == uid:
                return option["label"]
        return uid

    def _mapping_names(self, uid: str) -> list[str]:
        """Return configured mapping names for one lock."""

        mappings = _copy_lock_user_mappings(self._config_entry.options)
        return [
            name
            for mapping in mappings.get(uid, [])
            if (name := _clean_string(mapping.get("name")))
        ]

    def _mapping_for_name(self, uid: str, name: str | None) -> dict[str, Any]:
        """Return one configured mapping by name."""

        if not name:
            return {}
        mappings = _copy_lock_user_mappings(self._config_entry.options)
        for mapping in mappings.get(uid, []):
            if _clean_string(mapping.get("name")) == name:
                return mapping
        return {}

    def _mapping_summary(self, uid: str | None) -> str:
        """Return a compact summary of configured mappings."""

        if uid is None:
            return "None"
        mappings = _copy_lock_user_mappings(self._config_entry.options)
        lines = []
        for mapping in mappings.get(uid, []):
            name = _clean_string(mapping.get("name"))
            ids = ", ".join(str(item) for item in mapping.get("ids", []))
            if name and ids:
                lines.append(f"{name}: {ids}")
        return "\n".join(lines) if lines else "None"

    def _recent_unknown_id_summary(self, uid: str | None) -> str:
        """Return recently observed unknown lock user ids for one lock."""

        if uid is None:
            return "None"
        coordinator = self._coordinator()
        if coordinator is None:
            return "None"
        recent_ids = coordinator.recent_unknown_lock_user_ids(uid)
        return ", ".join(recent_ids) if recent_ids else "None"


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
            vol.Required(
                CONF_IMAGE_ROTATION,
                default=str(user_input.get(CONF_IMAGE_ROTATION, DEFAULT_IMAGE_ROTATION)),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[str(rotation) for rotation in IMAGE_ROTATIONS],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_LOCAL_PUSH_ENABLED,
                default=user_input.get(CONF_LOCAL_PUSH_ENABLED, DEFAULT_LOCAL_PUSH_ENABLED),
            ): bool,
            vol.Required(CONF_TIMEOUT, default=user_input.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)): vol.All(
                vol.Coerce(int), vol.Range(min=5, max=120)
            ),
        }
    )


def _general_options_schema(config_entry: config_entries.ConfigEntry) -> vol.Schema:
    """Return the general options form schema."""

    return vol.Schema(
        {
            vol.Required(
                CONF_REGION,
                default=config_entry.options.get(
                    CONF_REGION,
                    config_entry.data.get(CONF_REGION, DEFAULT_REGION),
                ),
            ): selector.SelectSelector(selector.SelectSelectorConfig(options=REGIONS)),
            vol.Required(
                CONF_SCAN_INTERVAL,
                default=config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): vol.All(vol.Coerce(int), vol.Range(min=15, max=3600)),
            vol.Required(
                CONF_EVENT_SCAN_INTERVAL,
                default=config_entry.options.get(CONF_EVENT_SCAN_INTERVAL, DEFAULT_EVENT_SCAN_INTERVAL),
            ): vol.All(vol.Coerce(int), vol.Range(min=30, max=3600)),
            vol.Required(
                CONF_IMAGE_ROTATION,
                default=str(config_entry.options.get(CONF_IMAGE_ROTATION, DEFAULT_IMAGE_ROTATION)),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[str(rotation) for rotation in IMAGE_ROTATIONS],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_LOCAL_PUSH_ENABLED,
                default=config_entry.options.get(CONF_LOCAL_PUSH_ENABLED, DEFAULT_LOCAL_PUSH_ENABLED),
            ): bool,
            vol.Required(
                CONF_TIMEOUT,
                default=config_entry.options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
            ): vol.All(vol.Coerce(int), vol.Range(min=5, max=120)),
        }
    )


def _copy_lock_user_mappings(options: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Return a normalized copy of configured lock user mappings."""

    mappings = options.get(CONF_LOCK_USER_MAPPINGS)
    if not isinstance(mappings, dict):
        return {}

    copied: dict[str, list[dict[str, Any]]] = {}
    for uid, device_mappings in mappings.items():
        uid = _clean_string(uid)
        if not uid or not isinstance(device_mappings, list):
            continue
        copied[uid] = []
        for mapping in device_mappings:
            if not isinstance(mapping, dict):
                continue
            name = _clean_string(mapping.get("name"))
            ids = _parse_id_list(mapping.get("ids"))
            if not name or not ids:
                continue
            copied_mapping: dict[str, Any] = {"name": name, "ids": ids}
            if person := _clean_string(mapping.get("person")):
                copied_mapping["person"] = person
            copied[uid].append(copied_mapping)
        if not copied[uid]:
            copied.pop(uid)
    return copied


def _parse_id_list(value: Any) -> list[str]:
    """Parse a comma, whitespace, or newline separated credential id list."""

    if isinstance(value, list):
        parts = [str(item) for item in value]
    else:
        parts = re.split(r"[\s,]+", str(value or ""))

    ids: list[str] = []
    for part in parts:
        part = part.strip()
        if part and part not in ids:
            ids.append(part)
    return ids


def _id_list_string(value: Any) -> str:
    """Return credential ids as one editable text value."""

    return "\n".join(_parse_id_list(value))


def _clean_string(value: Any) -> str:
    """Return a stripped string for config flow values."""

    return str(value or "").strip()


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
