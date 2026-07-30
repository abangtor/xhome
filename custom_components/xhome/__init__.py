"""Home Assistant custom integration for XHome/Lancens devices."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, PLATFORMS, SERVICE_REFRESH
from .coordinator import XHomeDataUpdateCoordinator

XHomeConfigEntry = ConfigEntry

CONF_AUTH_TYPE = "auth_type"
CONF_AVATAR = "avatar"
CONF_BEGIN_TIME = "begin_time"
CONF_CONFIG_ENTRY_ID = "config_entry_id"
CONF_CONFIRM = "confirm"
CONF_DATA = "data"
CONF_END_TIME = "end_time"
CONF_ENTRY = "entry"
CONF_EVENT_USER_ID = "event_user_id"
CONF_IDS = "ids"
CONF_KEY_ID = "key_id"
CONF_LOCK_TYPE = "lock_type"
CONF_MEMBER_TYPE = "member_type"
CONF_MODEL = "model"
CONF_NAME = "name"
CONF_RAND_KEY = "rand_key"
CONF_REMARKS = "remarks"
CONF_SDK = "sdk"
CONF_START_TIME = "start_time"
CONF_STOP_TIME = "stop_time"
CONF_TEMPORARY_PASSWORD = "password"
CONF_TOTAL_TIMES = "total_times"
CONF_UID = "uid"
CONF_UNLOCK_TYPE = "unlock_type"
CONF_USER_TYPE = "user_type"
CONF_WEEK = "week"

SERVICE_ADD_TEMPORARY_PASSWORD_RAW = "add_temporary_password_raw"
SERVICE_ADD_TEMPORARY_PASSWORD = "add_temporary_password"
SERVICE_DELETE_TEMPORARY_PASSWORD = "delete_temporary_password"
SERVICE_GET_APP_LOCK_STATUS = "get_app_lock_status"
SERVICE_GET_SCREEN_LIGHT_CONFIG = "get_screen_light_config"
SERVICE_LIST_DEVICES = "list_devices"
SERVICE_LIST_LOCK_MEMBERS = "list_lock_members"
SERVICE_LOCAL_PUSH_STATUS = "local_push_status"
SERVICE_LIST_TEMPORARY_PASSWORDS = "list_temporary_passwords"
SERVICE_RENAME_TEMPORARY_PASSWORD = "rename_temporary_password"
SERVICE_SET_UNLOCK_TYPE = "set_unlock_type"
SERVICE_UPDATE_EVENT_MEMBER = "update_event_member"
SERVICE_UPSERT_LOCK_MEMBER = "upsert_lock_member"

_OPTIONAL_CONFIG_ENTRY = {vol.Optional(CONF_CONFIG_ENTRY_ID): cv.string}
_UID_SCHEMA = {**_OPTIONAL_CONFIG_ENTRY, vol.Required(CONF_UID): cv.string}
_ENTRY_SCHEMA = {vol.Optional(CONF_ENTRY, default="app"): cv.string}
_IDS_SCHEMA = vol.Any(cv.string, int, [vol.Any(cv.string, int)])


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up integration-level services."""

    hass.data.setdefault(DOMAIN, {})

    async def handle_refresh(call: ServiceCall) -> None:
        """Refresh all configured XHome coordinators."""

        for coordinator in list(hass.data[DOMAIN].values()):
            await coordinator.async_request_refresh()

    hass.services.async_register(DOMAIN, SERVICE_REFRESH, handle_refresh)
    _register_api_services(hass)
    return True


def _register_api_services(hass: HomeAssistant) -> None:
    """Register XHome API helper services."""

    async def handle_get_screen_light_config(call: ServiceCall) -> dict[str, Any]:
        return await _call_client_response(
            hass,
            call,
            "screen light config",
            "get_screen_light_config",
            call.data[CONF_UID],
            uid=call.data[CONF_UID],
        )

    async def handle_list_devices(call: ServiceCall) -> dict[str, Any]:
        coordinator = _coordinator_for_service(hass, call)
        if coordinator.data is None:
            await coordinator.async_request_refresh()
        if coordinator.data is None:
            raise HomeAssistantError("XHome device data is unavailable")

        devices = []
        for data in coordinator.data.devices.values():
            devices.append(
                {
                    "name": data.name,
                    "uid": data.uid,
                    "device_id": data.device_id,
                    "model": data.model,
                    "online_type": data.first("online_type", "onlineType"),
                    "battery": data.first("battery", "bat"),
                    "rssi": data.first("rssi", "wifi_rssi"),
                }
            )
        return {"devices": devices}

    async def handle_local_push_status(call: ServiceCall) -> dict[str, Any]:
        coordinator = _coordinator_for_service(hass, call)
        return coordinator.local_push_status()

    async def handle_get_app_lock_status(call: ServiceCall) -> dict[str, Any]:
        return await _call_client_response(
            hass,
            call,
            "app lock status",
            "get_app_lock_status",
            call.data.get("brand", "python"),
            call.data.get(CONF_MODEL, "xhome-api"),
            call.data.get(CONF_SDK, 35),
        )

    async def handle_set_unlock_type(call: ServiceCall) -> dict[str, Any]:
        return await _call_client_response(
            hass,
            call,
            "unlock type",
            "set_unlock_type",
            call.data[CONF_UNLOCK_TYPE],
            refresh=True,
        )

    async def handle_list_lock_members(call: ServiceCall) -> dict[str, Any]:
        uid = call.data.get(CONF_UID)
        if uid:
            return await _call_client_response(
                hass,
                call,
                "lock members",
                "list_lock_members",
                uid,
                uid=uid,
            )
        return await _call_client_response(hass, call, "all lock members", "list_all_lock_members")

    async def handle_upsert_lock_member(call: ServiceCall) -> dict[str, Any]:
        return await _call_client_response(
            hass,
            call,
            "lock member update",
            "upsert_lock_member",
            call.data[CONF_UID],
            uid=call.data[CONF_UID],
            remarks=call.data[CONF_REMARKS],
            avatar=call.data.get(CONF_AVATAR, ""),
            lock_type=call.data.get(CONF_LOCK_TYPE, 0),
            event_user_id=call.data.get(CONF_EVENT_USER_ID, 0),
            member_type=call.data.get(CONF_MEMBER_TYPE, 0),
            model=call.data.get(CONF_MODEL, 0),
            key_id=call.data.get(CONF_KEY_ID),
        )

    async def handle_update_event_member(call: ServiceCall) -> dict[str, Any]:
        return await _call_client_response(
            hass,
            call,
            "event member update",
            "update_event_member",
            call.data[CONF_UID],
            call.data[CONF_EVENT_USER_ID],
            call.data[CONF_MEMBER_TYPE],
            call.data[CONF_REMARKS],
            uid=call.data[CONF_UID],
        )

    async def handle_list_temporary_passwords(call: ServiceCall) -> dict[str, Any]:
        uid = call.data.get(CONF_UID)
        entry = call.data[CONF_ENTRY]
        if uid:
            return await _call_client_response(
                hass,
                call,
                "temporary passwords",
                "list_temporary_passwords",
                uid,
                entry,
                uid=uid,
            )
        return await _call_client_response(
            hass,
            call,
            "all temporary passwords",
            "list_all_temporary_passwords",
            entry,
        )

    async def handle_add_temporary_password_raw(call: ServiceCall) -> dict[str, Any]:
        _require_confirmed(call)
        return await _call_client_response(
            hass,
            call,
            "temporary password add",
            "add_temporary_password_raw",
            call.data[CONF_UID],
            uid=call.data[CONF_UID],
            name=call.data[CONF_NAME],
            data=call.data[CONF_DATA],
            rand_key=call.data[CONF_RAND_KEY],
            begin_time=call.data.get(CONF_BEGIN_TIME, 0),
            end_time=call.data.get(CONF_END_TIME, 0),
            start_time=call.data.get(CONF_START_TIME, 0),
            stop_time=call.data.get(CONF_STOP_TIME, 0),
            total_times=call.data.get(CONF_TOTAL_TIMES, 0),
            week=call.data.get(CONF_WEEK, 0),
            user_type=call.data.get(CONF_USER_TYPE, 2),
            auth_type=call.data.get(CONF_AUTH_TYPE, 1),
            entry=call.data[CONF_ENTRY],
        )

    async def handle_add_temporary_password(call: ServiceCall) -> dict[str, Any]:
        _require_confirmed(call)
        return await _call_client_response(
            hass,
            call,
            "temporary password add",
            "add_temporary_password",
            call.data[CONF_UID],
            uid=call.data[CONF_UID],
            name=call.data[CONF_NAME],
            password=call.data[CONF_TEMPORARY_PASSWORD],
            begin_time=call.data.get(CONF_BEGIN_TIME, 0),
            end_time=call.data.get(CONF_END_TIME, 0),
            start_time=call.data.get(CONF_START_TIME, 0),
            stop_time=call.data.get(CONF_STOP_TIME, 0),
            total_times=call.data.get(CONF_TOTAL_TIMES, 0),
            week=call.data.get(CONF_WEEK, 0),
            user_type=call.data.get(CONF_USER_TYPE, 2),
            auth_type=call.data.get(CONF_AUTH_TYPE, 1),
            entry=call.data[CONF_ENTRY],
        )

    async def handle_rename_temporary_password(call: ServiceCall) -> dict[str, Any]:
        return await _call_client_response(
            hass,
            call,
            "temporary password rename",
            "rename_temporary_password",
            call.data[CONF_UID],
            call.data[CONF_IDS],
            call.data[CONF_NAME],
            call.data[CONF_ENTRY],
            uid=call.data[CONF_UID],
        )

    async def handle_delete_temporary_password(call: ServiceCall) -> dict[str, Any]:
        _require_confirmed(call)
        return await _call_client_response(
            hass,
            call,
            "temporary password delete",
            "delete_temporary_password",
            call.data[CONF_UID],
            call.data[CONF_IDS],
            call.data[CONF_ENTRY],
            uid=call.data[CONF_UID],
        )

    _register_service(
        hass,
        SERVICE_LIST_DEVICES,
        handle_list_devices,
        _OPTIONAL_CONFIG_ENTRY,
        SupportsResponse.ONLY,
    )
    _register_service(
        hass,
        SERVICE_LOCAL_PUSH_STATUS,
        handle_local_push_status,
        _OPTIONAL_CONFIG_ENTRY,
        SupportsResponse.ONLY,
    )
    _register_service(
        hass,
        SERVICE_GET_SCREEN_LIGHT_CONFIG,
        handle_get_screen_light_config,
        _UID_SCHEMA,
        SupportsResponse.ONLY,
    )
    _register_service(
        hass,
        SERVICE_GET_APP_LOCK_STATUS,
        handle_get_app_lock_status,
        {
            **_OPTIONAL_CONFIG_ENTRY,
            vol.Optional("brand", default="python"): cv.string,
            vol.Optional(CONF_MODEL, default="xhome-api"): cv.string,
            vol.Optional(CONF_SDK, default=35): vol.Coerce(int),
        },
        SupportsResponse.ONLY,
    )
    _register_service(
        hass,
        SERVICE_SET_UNLOCK_TYPE,
        handle_set_unlock_type,
        {**_OPTIONAL_CONFIG_ENTRY, vol.Required(CONF_UNLOCK_TYPE): vol.Coerce(int)},
    )
    _register_service(
        hass,
        SERVICE_LIST_LOCK_MEMBERS,
        handle_list_lock_members,
        {**_OPTIONAL_CONFIG_ENTRY, vol.Optional(CONF_UID): cv.string},
        SupportsResponse.ONLY,
    )
    _register_service(
        hass,
        SERVICE_UPSERT_LOCK_MEMBER,
        handle_upsert_lock_member,
        {
            **_UID_SCHEMA,
            vol.Required(CONF_REMARKS): cv.string,
            vol.Optional(CONF_AVATAR, default=""): cv.string,
            vol.Optional(CONF_LOCK_TYPE, default=0): vol.Coerce(int),
            vol.Optional(CONF_EVENT_USER_ID, default=0): vol.Coerce(int),
            vol.Optional(CONF_MEMBER_TYPE, default=0): vol.Coerce(int),
            vol.Optional(CONF_MODEL, default=0): vol.Coerce(int),
            vol.Optional(CONF_KEY_ID): vol.Coerce(int),
        },
    )
    _register_service(
        hass,
        SERVICE_UPDATE_EVENT_MEMBER,
        handle_update_event_member,
        {
            **_UID_SCHEMA,
            vol.Required(CONF_EVENT_USER_ID): vol.Coerce(int),
            vol.Required(CONF_MEMBER_TYPE): vol.Coerce(int),
            vol.Required(CONF_REMARKS): cv.string,
        },
    )
    _register_service(
        hass,
        SERVICE_LIST_TEMPORARY_PASSWORDS,
        handle_list_temporary_passwords,
        {**_OPTIONAL_CONFIG_ENTRY, vol.Optional(CONF_UID): cv.string, **_ENTRY_SCHEMA},
        SupportsResponse.ONLY,
    )
    _register_service(
        hass,
        SERVICE_ADD_TEMPORARY_PASSWORD_RAW,
        handle_add_temporary_password_raw,
        {
            **_UID_SCHEMA,
            **_ENTRY_SCHEMA,
            vol.Required(CONF_NAME): cv.string,
            vol.Required(CONF_DATA): cv.string,
            vol.Required(CONF_RAND_KEY): cv.string,
            vol.Required(CONF_CONFIRM): cv.boolean,
            vol.Optional(CONF_BEGIN_TIME, default=0): vol.Coerce(int),
            vol.Optional(CONF_END_TIME, default=0): vol.Coerce(int),
            vol.Optional(CONF_START_TIME, default=0): vol.Coerce(int),
            vol.Optional(CONF_STOP_TIME, default=0): vol.Coerce(int),
            vol.Optional(CONF_TOTAL_TIMES, default=0): vol.Coerce(int),
            vol.Optional(CONF_WEEK, default=0): vol.Coerce(int),
            vol.Optional(CONF_USER_TYPE, default=2): vol.Coerce(int),
            vol.Optional(CONF_AUTH_TYPE, default=1): vol.Coerce(int),
        },
    )
    _register_service(
        hass,
        SERVICE_ADD_TEMPORARY_PASSWORD,
        handle_add_temporary_password,
        {
            **_UID_SCHEMA,
            **_ENTRY_SCHEMA,
            vol.Required(CONF_NAME): cv.string,
            vol.Required(CONF_TEMPORARY_PASSWORD): cv.string,
            vol.Required(CONF_CONFIRM): cv.boolean,
            vol.Optional(CONF_BEGIN_TIME, default=0): vol.Coerce(int),
            vol.Optional(CONF_END_TIME, default=0): vol.Coerce(int),
            vol.Optional(CONF_START_TIME, default=0): vol.Coerce(int),
            vol.Optional(CONF_STOP_TIME, default=0): vol.Coerce(int),
            vol.Optional(CONF_TOTAL_TIMES, default=0): vol.Coerce(int),
            vol.Optional(CONF_WEEK, default=0): vol.Coerce(int),
            vol.Optional(CONF_USER_TYPE, default=2): vol.Coerce(int),
            vol.Optional(CONF_AUTH_TYPE, default=1): vol.Coerce(int),
        },
    )
    _register_service(
        hass,
        SERVICE_RENAME_TEMPORARY_PASSWORD,
        handle_rename_temporary_password,
        {
            **_UID_SCHEMA,
            **_ENTRY_SCHEMA,
            vol.Required(CONF_IDS): _IDS_SCHEMA,
            vol.Required(CONF_NAME): cv.string,
        },
    )
    _register_service(
        hass,
        SERVICE_DELETE_TEMPORARY_PASSWORD,
        handle_delete_temporary_password,
        {
            **_UID_SCHEMA,
            **_ENTRY_SCHEMA,
            vol.Required(CONF_IDS): _IDS_SCHEMA,
            vol.Required(CONF_CONFIRM): cv.boolean,
        },
    )


def _register_service(
    hass: HomeAssistant,
    service: str,
    handler: Any,
    schema: dict[Any, Any],
    supports_response: SupportsResponse = SupportsResponse.OPTIONAL,
) -> None:
    """Register one XHome service."""

    hass.services.async_register(
        DOMAIN,
        service,
        handler,
        schema=vol.Schema(schema),
        supports_response=supports_response,
    )


async def _call_client_response(
    hass: HomeAssistant,
    call: ServiceCall,
    call_name: str,
    method_name: str,
    *args: Any,
    uid: str | None = None,
    refresh: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Run one coordinator client call and wrap the result for service responses."""

    coordinator = _coordinator_for_service(hass, call, uid=uid)
    result = await coordinator.async_call_client(call_name, method_name, *args, refresh=refresh, **kwargs)
    return {"result": result}


def _coordinator_for_service(
    hass: HomeAssistant,
    call: ServiceCall,
    *,
    uid: str | None = None,
) -> XHomeDataUpdateCoordinator:
    """Return the coordinator selected by config entry id, device uid, or singleton setup."""

    coordinators = hass.data.get(DOMAIN, {})
    entry_id = call.data.get(CONF_CONFIG_ENTRY_ID)
    if entry_id:
        coordinator = coordinators.get(entry_id)
        if coordinator is None:
            raise HomeAssistantError(f"XHome config entry {entry_id!r} is not loaded")
        return coordinator

    loaded = [
        coordinator
        for coordinator in coordinators.values()
        if isinstance(coordinator, XHomeDataUpdateCoordinator)
    ]
    if uid:
        for coordinator in loaded:
            if coordinator.data is not None and uid in coordinator.data.devices:
                return coordinator

    if len(loaded) == 1:
        return loaded[0]

    raise HomeAssistantError("Pass config_entry_id to choose an XHome account")


def _require_confirmed(call: ServiceCall) -> None:
    """Require explicit confirmation for access-changing services."""

    if call.data.get(CONF_CONFIRM) is not True:
        raise HomeAssistantError("Set confirm: true to run this access-changing XHome service")


async def async_setup_entry(hass: HomeAssistant, entry: XHomeConfigEntry) -> bool:
    """Set up XHome from a config entry."""

    coordinator = XHomeDataUpdateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await coordinator.async_seed_events()
    entry.async_on_unload(coordinator.async_start_local_push())
    entry.async_on_unload(coordinator.async_start_event_polling())
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
