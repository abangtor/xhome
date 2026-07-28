"""Synchronous client for the XHome/Lancens REST API."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urljoin

import requests

from .constants import API_KEY, BUNDLE_ID, ROUTE_BASE_URLS, normalize_region
from .exceptions import XHomeAPIError, XHomeAuthError
from .models import LoginSession, Region
from .signing import login_sign, token_time_sign

JSON = dict[str, Any] | list[Any] | str | int | float | bool | None


class XHomeClient:
    """Client for the REST portion of the XHome API.

    The Android app uses native libraries for P2P video/control. This class only
    implements cloud REST calls.
    """

    def __init__(
        self,
        region: str | int | Region = "china",
        *,
        token: str | None = None,
        user_id: int | str | None = None,
        base_url: str | None = None,
        timeout: float = 30,
        session: requests.Session | None = None,
        raise_api_errors: bool = True,
    ) -> None:
        self.region = normalize_region(region)
        self.base_url = (base_url or self.region.rest_url).rstrip("/") + "/"
        self.timeout = timeout
        self.session = session or requests.Session()
        self.token = token
        self.user_id = int(user_id) if user_id not in (None, "") else None
        self.username: str | None = None
        self.raise_api_errors = raise_api_errors

    def login(self, username: str, password: str, *, timestamp: int | None = None) -> LoginSession:
        """Log in with the normal username/password flow and store the token."""

        timestamp = int(time.time()) if timestamp is None else int(timestamp)
        body = {
            "username": username,
            "time": timestamp,
            "apikey": API_KEY,
            "sign": login_sign(username, password, timestamp),
        }
        raw = self.post("/v2/api/user/login/new", body, auth=False)
        payload = unwrap_response(raw)
        if not isinstance(payload, dict):
            raise XHomeAPIError("Login returned an unexpected response shape", payload=raw)

        token = payload.get("token")
        if not token:
            raise XHomeAPIError("Login response did not contain a token", payload=raw)

        user_id = _optional_int(payload.get("id") or payload.get("user_id") or payload.get("users_id"))
        self.username = username
        self.token = str(token)
        self.user_id = user_id

        return LoginSession(
            user_id=user_id,
            token=self.token,
            refresh_key=_optional_str(payload.get("refresh_key")),
            logout_status=_optional_int(payload.get("logout_status")),
            server_time=_optional_int(payload.get("time")),
            raw=dict(payload),
        )

    def logout(self, *, username: str | None = None) -> JSON:
        body = {"token": self.require_token()}
        username = username or self.username
        if username:
            body["username"] = username
        result = self.post("v1/api/user/logout", body)
        self.token = None
        return result

    def get_current_user(self) -> JSON:
        return self.get("v1/api/user")

    def change_password(self, old_password: str, new_password: str) -> JSON:
        return self.put("v1/api/user/password", {"oldpassword": old_password, "password": new_password})

    def change_username(self, username: str) -> JSON:
        return self.put("v1/api/user/username", {"username": username})

    def list_all_devices(self) -> JSON:
        return self.get("v1/api/user/all/device/list")

    def list_devices_resilient(self) -> JSON:
        """Return the richest available device list, falling back when needed."""

        try:
            return self.list_all_devices()
        except XHomeAPIError as err:
            if err.status_code != 400:
                raise
        return self.list_devices()

    def list_devices(self) -> JSON:
        """Return the main-device list endpoint."""

        return self.get("v1/api/user/device")

    def flatten_devices(self, payload: JSON | None = None) -> list[dict[str, Any]]:
        """Flatten either XHome device-list response shape into one list."""

        data = unwrap_response(self.list_devices_resilient() if payload is None else payload)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if not isinstance(data, dict):
            return []

        devices: list[dict[str, Any]] = []
        for key in ("mainList", "shareList"):
            value = data.get(key)
            if isinstance(value, list):
                devices.extend(item for item in value if isinstance(item, dict))
        return devices

    def add_device(self, **fields: Any) -> JSON:
        return self.post("v1/api/user/device", fields)

    def update_device(self, device_id: int, **fields: Any) -> JSON:
        return self.put(f"v1/api/user/device/{device_id}", _without_none(fields))

    def delete_device(self, device_id: int) -> JSON:
        return self.delete(f"v1/api/user/device/{device_id}")

    def set_device_name(self, uid: str, name: str) -> JSON:
        return self.post("v1/api/user/update/device", {"uid": uid, "name": name})

    def get_device_detail(self, uuid: str) -> JSON:
        return self.get("v1/api/app/one/device/info", params={"uuid": uuid})

    def get_firmware(self, uuid: str, language: str = "en") -> JSON:
        return self.get("v1/api/app/firmware", params={"uuid": uuid, "language": language})

    def get_ble_ota_path(self, blename: str) -> JSON:
        return self.post("/v1/api/users/device/ota", {"blename": blename})

    def get_device_logos(self, device_type: int) -> JSON:
        return self.get("v1/api/device/logo_list", params={"type": device_type})

    def list_networks(self, language: str = "en") -> JSON:
        return self.get("v1/api/all/network/list", params={"language": language})

    def submit_device_address(self, uuid: str, address: str) -> JSON:
        return self.post("/v1/api/users/device/address", {"uuid": uuid, "address": address})

    def get_online_status(self, uid: str, online_type: str | int = "1") -> JSON:
        return self.get("v1/api/device/online", params={"uid": uid, "online_type": str(online_type)}, auth=False)

    def get_device_token(self, *, device_id: int | None = None, uid: str | None = None) -> JSON:
        if (device_id is None) == (uid is None):
            raise ValueError("Pass exactly one of device_id or uid")
        if uid is not None:
            return self.get(f"v1/api/user/device/all/{uid}/token")
        return self.get(f"v1/api/user/device/{device_id}/token")

    def get_record_time(self, uuid: str) -> JSON:
        return self.get("/v1/api/users/device/record/time", params={"uuid": uuid})

    def get_new_device_events(self, uuid: str, device_type: str) -> JSON:
        return self.post("v1/api/user/device/event/new", {"uuid": uuid, "type": device_type})

    def get_events_by_time(
        self,
        *,
        raw_time: int,
        start_time: int,
        end_time: int,
        page_number: int = 20,
        page: int = 0,
    ) -> JSON:
        return self.post(
            "v1/api/user/app/event/time/stamp",
            {
                "raw_time": raw_time,
                "start_time": start_time,
                "end_time": end_time,
                "page": page,
                "page_number": page_number,
            },
        )

    def get_event_totals(self, *, time_zone: str | int, time_zone_offset: str | int) -> JSON:
        return self.post(
            "v1/api/user/device/event/total/time/stamp",
            {"time_zone": time_zone, "time_zone_offset": time_zone_offset},
        )

    def get_media_url(self, uuid: str, event_guid: str) -> JSON:
        return self.post("v1/api/app/device/oss/list", {"uuid": uuid, "event_guid": event_guid})

    def delete_event_records(self, *, start_time: int, end_time: int, ids: str) -> JSON:
        return self.post(
            "v1/api/user/delete/event/record",
            {"start_time": start_time, "end_time": end_time, "ids": ids},
        )

    def delete_device_event_records(self, device_id: int) -> JSON:
        return self.delete(f"v1/api/user/devices/{device_id}/event_record")

    def set_notify_control(self, device_id: int, notify_ctrl: int) -> JSON:
        return self.post(f"/v1/api/device/notify_ctrl/{device_id}", {"notify_ctrl": notify_ctrl})

    def set_share_notify_control(self, uid: str, notify_ctrl: int) -> JSON:
        return self.post(f"/v1/api/device/share/notify_ctrl/{uid}", {"notify_ctrl": notify_ctrl})

    def get_screen_light_config(self, uid: str) -> JSON:
        return self.get("v1/api/device/screen/light", params={"uid": uid})

    def set_screen_light_timeout(self, uid: str, timeout_seconds: int) -> JSON:
        return self.post("v1/api/device/screen/light", {"uid": uid, "screenon_timeout": timeout_seconds})

    def set_battery_display(self, uuid: str, enabled: bool | int) -> JSON:
        return self.post("v1/api/device/battery/status", {"uuid": uuid, "bat_display_en": _flag(enabled)})

    def set_wet_play(self, uuid: str, enabled: bool | int) -> JSON:
        return self.post("v1/api/device/wet_play/status", {"uuid": uuid, "wet_play": _flag(enabled)})

    def set_call_screen(self, uuid: str, enabled: bool | int) -> JSON:
        return self.post("v1/api/device/call/screen/status", {"uuid": uuid, "call_screen_on": _flag(enabled)})

    def set_standby_mode(self, uid: str, standby_mode: int) -> JSON:
        return self.post("v1/api/device/standby_mode/status", {"uid": uid, "standby_mode": standby_mode})

    def set_target_ev(self, uid: str, target_ev: int) -> JSON:
        return self.post("v1/api/device/target/ev", {"uid": uid, "target_ev": target_ev})

    def set_unlock_type(self, unlock_type: int) -> JSON:
        return self.post("v1/api/user/unlock/status", {"unlock_type": unlock_type})

    def set_device_unlock_limit(self, uuid: str, unlock_limit: int) -> JSON:
        return self.post("v1/api/device/unlock/status", {"uuid": uuid, "unlock_limit": unlock_limit})

    def set_remote_unlock_limit(self, uuid: str, unlock_limit: int) -> JSON:
        """Set the app's per-device remote-unlock limit.

        The Android UI appears to use ``0`` for remote unlock anytime and ``1``
        for remote unlock only after a doorbell/call event.
        """

        return self.set_device_unlock_limit(uuid, unlock_limit)

    def get_app_lock_status(self, brand: str = "python", model: str = "xhome-api", sdk: int = 35) -> JSON:
        return self.get("v1/api/app/lock/status", params={"brand": brand.lower(), "model": model.lower(), "sdk": sdk})

    def open_lock(self, uuid: str, *, timestamp: int | None = None) -> JSON:
        timestamp = int(time.time()) if timestamp is None else int(timestamp)
        token = self.require_token()
        return self.post(
            "v1/api/app/open/lock",
            {
                "apikey": API_KEY,
                "uuid": uuid,
                "time": timestamp,
                "sign": token_time_sign(token, uuid, timestamp),
            },
        )

    def unlock_door(self, uuid: str, *, timestamp: int | None = None) -> JSON:
        """Unlock a door device via the cloud API.

        This is a semantic alias for the Android app's "open lock" request,
        named for Home Assistant lock entity use.
        """

        return self.open_lock(uuid, timestamp=timestamp)

    def search_user(self, username: str) -> JSON:
        return self.post("v1/api/user/friend/username", {"username": username})

    def add_friend(self, suid: int, *, uid: int | None = None, status: int = 1) -> JSON:
        return self.post("v1/api/device/share/friend", {"uid": uid or self.user_id, "suid": suid, "status": status})

    def delete_friend(self, friend_id: int, *, mode: str = "share") -> JSON:
        if mode == "friend":
            return self.delete(f"v1/api/add/friend/{friend_id}/friend")
        if mode == "share":
            return self.delete(f"/v1/api/add/share/friend/{friend_id}")
        if mode == "device":
            return self.delete(f"v1/api/device/share/friend/{friend_id}")
        raise ValueError("mode must be one of: share, friend, device")

    def list_share_friends(self) -> JSON:
        return self.get("v1/api/device/share/friend")

    def list_shielded_friends(self, uid: str) -> JSON:
        return self.get("v1/api/shield/share/friend", params={"uid": uid})

    def select_share_friend(self, name: str) -> JSON:
        return self.get("v1/api/selecte/share/friend", params={"name": name})

    def add_device_share(self, uuid: str) -> JSON:
        return self.post("v1/api/app/device/add/share", {"uuid": uuid, "entry": "app"}, idcode=True)

    def delete_app_device_share(self, uid: str) -> JSON:
        return self.delete(f"v1/api/app/device/share/{uid}")

    def list_shared_devices(self, uid: str) -> JSON:
        return self.get("v1/api/device/share/device", params={"uid": uid})

    def share_device_with_friend(self, uid: str, suid: int, status: int = 1) -> JSON:
        return self.post("v1/api/device/share/friend", {"uid": uid, "suid": suid, "status": status})

    def update_share_friend_realm(self, share_friend_id: int, realm: str) -> JSON:
        return self.put(f"v1/api/device/share/friend/{share_friend_id}", {"realm": realm})

    def rename_shared_device(self, uid: str, remarkname: str) -> JSON:
        return self.put(f"v1/api/device/share/name/{uid}", {"remarkname": remarkname})

    def list_group_shares(self) -> JSON:
        return self.get("v1/api/device/app/group/share")

    def get_group_share(self, group: str) -> JSON:
        return self.get("v1/api/device/share/group", params={"group": group})

    def upsert_group_share(self, group_name: str, **fields: Any) -> JSON:
        body = {"group_name": group_name, **fields}
        return self.put(f"v1/api/device/share/group/{group_name}", body)

    def push_app_share(self, **fields: Any) -> JSON:
        return self.post("v1/api/device/app/share", fields)

    def delete_group_share(self, group: str) -> JSON:
        return self.delete(f"v1/api/device/delete/group/share/{group}")

    def transfer_device(self, **fields: Any) -> JSON:
        return self.post("v1/api/device/app/transfer", fields)

    def list_all_device_members(self) -> JSON:
        return self.get("v1/api/device/member/list/all")

    def list_all_lock_members(self) -> JSON:
        """Return all known lock/member records for the account."""

        return self.list_all_device_members()

    def list_device_members(self, uuid: str) -> JSON:
        return self.post("v1/api/users/device/member/list", {"uuid": uuid})

    def list_lock_members(self, uuid: str) -> JSON:
        """Return lock/member records for one device."""

        return self.list_device_members(uuid)

    def upsert_member(
        self,
        uuid: str,
        *,
        remarks: str,
        avatar: str = "",
        lock_type: int = 0,
        event_user_id: int = 0,
        member_type: int = 0,
        model: int = 0,
        key_id: int | None = None,
    ) -> JSON:
        body = {
            "uuid": uuid,
            "remarks": remarks,
            "avatar": avatar,
            "lock_type": lock_type,
            "event_user_id": event_user_id,
            "member_type": member_type,
            "model": model,
        }
        if key_id is not None:
            body["key_id"] = key_id
            return self.post("v1/api/users/device/member/update/new", body)
        return self.post("v1/api/users/device/member/update", body)

    def upsert_lock_member(self, uuid: str, **fields: Any) -> JSON:
        """Add or update a lock member.

        This is a semantic alias for the Android app's member update endpoint.
        """

        return self.upsert_member(uuid, **fields)

    def update_event_member(self, uid: str, event_user_id: int, member_type: int, remarks: str) -> JSON:
        return self.post(
            "v1/api/users/event/member",
            {"uid": uid, "event_user_id": event_user_id, "member_type": member_type, "remarks": remarks},
        )

    def list_auth(self, uuid: str, entry: str = "app") -> JSON:
        return self.get("v1/api/device/iviews/auth/list", params={"uuid": uuid, "entry": entry})

    def list_all_auth(self, entry: str = "app") -> JSON:
        return self.get("v1/api/device/iviews/auth/all", params={"entry": entry})

    def add_auth_raw(self, uuid: str, **fields: Any) -> JSON:
        body = {"uuid": uuid, "entry": "app", **fields}
        return self.post("v1/api/device/iviews/auth/add", body)

    def list_temporary_passwords(self, uuid: str, entry: str = "app") -> JSON:
        """Return temporary-password/auth records for one device."""

        return self.list_auth(uuid, entry=entry)

    def list_all_temporary_passwords(self, entry: str = "app") -> JSON:
        """Return temporary-password/auth records for all devices."""

        return self.list_all_auth(entry=entry)

    def add_temporary_password_raw(
        self,
        uuid: str,
        *,
        name: str,
        data: str,
        rand_key: str,
        begin_time: int = 0,
        end_time: int = 0,
        start_time: int = 0,
        stop_time: int = 0,
        total_times: int = 0,
        week: int = 0,
        user_type: int = 2,
        auth_type: int = 1,
        entry: str = "app",
    ) -> JSON:
        """Submit a pre-encoded temporary password/auth record.

        The Android app generates ``data`` with the native ``IVIEWSPassword``
        library. This client can submit an already encoded blob, but it cannot
        generate that blob without the native algorithm.
        """

        return self.add_auth_raw(
            uuid,
            entry=entry,
            name=name,
            begin_time=begin_time,
            end_time=end_time,
            start_time=start_time,
            stop_time=stop_time,
            total_times=total_times,
            week=week,
            user_type=user_type,
            auth_type=auth_type,
            data=data,
            rand_key=rand_key,
        )

    def update_auth_name(self, uuid: str, ids: str | int | list[int], name: str, entry: str = "app") -> JSON:
        return self.post(
            "v1/api/device/iviews/auth/update",
            {"uuid": uuid, "entry": entry, "ids": _ids(ids), "name": name},
        )

    def rename_temporary_password(self, uuid: str, ids: str | int | list[int], name: str, entry: str = "app") -> JSON:
        """Rename one or more temporary-password/auth records."""

        return self.update_auth_name(uuid, ids, name, entry=entry)

    def delete_auth(self, uuid: str, ids: str | int | list[int], entry: str = "app") -> JSON:
        return self.post("v1/api/device/iviews/auth/del", {"uuid": uuid, "entry": entry, "ids": _ids(ids)})

    def delete_temporary_password(self, uuid: str, ids: str | int | list[int], entry: str = "app") -> JSON:
        """Delete one or more temporary-password/auth records."""

        return self.delete_auth(uuid, ids, entry=entry)

    def add_ble_lock_device(
        self,
        *,
        name: str,
        code: str,
        mac: str,
        longitude: str | float,
        latitude: str | float,
        time_zone: int,
        iviews_func: int,
        blename: str,
    ) -> JSON:
        return self.post(
            "v1/api/user/add/blelock/device",
            {
                "name": name,
                "code": code,
                "mac": mac,
                "longitude": str(longitude),
                "latitude": str(latitude),
                "time_zone": time_zone,
                "iviews_func": iviews_func,
                "blename": blename,
            },
        )

    def new_ble_lock_device(self, uuid: str, model: int) -> JSON:
        return self.post("v1/api/user/blelock/device/new", {"uuid": uuid, "model": model})

    def get_gms_list(self, uuid: str, model: int, language: str = "en") -> JSON:
        return self.post("v1/api/app/device/gms/list", {"uuid": uuid, "model": model, "language": language})

    def get_all_gms_list(self, language: str = "en") -> JSON:
        return self.post("v1/api/app/user/all/gms/list", {"language": language})

    def get_one_gms_info(self, model: int) -> JSON:
        return self.post("v1/api/app/user/one/gms/info", {"model": model})

    def change_gms_config(
        self,
        uuid: str,
        model: int,
        gms: int,
        *,
        target_uuid: str | None = None,
        timestamp: int | None = None,
    ) -> JSON:
        timestamp = int(time.time()) if timestamp is None else int(timestamp)
        token = self.require_token()
        user_id = self.require_user_id()
        return self.post(
            "v1/api/app/device/gms/change",
            {
                "apikey": API_KEY,
                "sign": token_time_sign(token, user_id, timestamp),
                "time": timestamp,
                "data": {"uuid": uuid, "model": model},
                "change_data": {"uuid": target_uuid or uuid, "gms": gms},
            },
        )

    def set_app_safe_password(self, password: str, safe_password: str, entry: str = "app") -> JSON:
        return self.post(
            "v1/api/user/app/safe",
            {"password": password, "safe_password": safe_password, "entry": entry},
        )

    def set_app_safe_lock(self, safe_password: str, entry: str = "app") -> JSON:
        return self.post("v1/api/app/safe/lock", {"safe_password": safe_password, "entry": entry})

    def register_push_token(
        self,
        push_token: str,
        *,
        push_platform: str = "FCM",
        os_token: str = "",
        language: str = "en",
        os_name: str = "ANDROID",
        os_push_version: int = 1,
        bundle_id: str = BUNDLE_ID,
        phone_model: str = "xhome-api",
    ) -> JSON:
        """Register the Lancens native push socket token for call events."""

        return self.post(
            "v1/api/user/token",
            _push_token_body(
                push_token,
                push_platform=push_platform,
                os_token=os_token,
                language=language,
                os_name=os_name,
                os_push_version=os_push_version,
                bundle_id=bundle_id,
                phone_model=phone_model,
            ),
        )

    def register_push_message_token(
        self,
        push_token: str,
        *,
        push_platform: str = "FCM",
        os_token: str = "",
        language: str = "en",
        os_name: str = "ANDROID",
        os_push_version: int = 1,
        bundle_id: str = BUNDLE_ID,
        phone_model: str = "xhome-api",
    ) -> JSON:
        """Register the Lancens native push socket token for notifications."""

        return self.post(
            "v1/api/user/message/token",
            _push_token_body(
                push_token,
                push_platform=push_platform,
                os_token=os_token,
                language=language,
                os_name=os_name,
                os_push_version=os_push_version,
                bundle_id=bundle_id,
                phone_model=phone_model,
            ),
        )

    def register_push_tokens(self, push_token: str, **kwargs: Any) -> dict[str, JSON]:
        """Register the native push token with both app token endpoints."""

        return {
            "call": self.register_push_token(push_token, **kwargs),
            "message": self.register_push_message_token(push_token, **kwargs),
        }

    def app_link(self, uid: str, status: int) -> JSON:
        return self.post(
            "v1/api/app/link",
            {"uid": uid, "status": status, "app": "Android", "user_id": self.user_id},
            route="show",
            auth=False,
        )

    def get_app_show_status(self) -> JSON:
        return self.get("v1/api/app/show/status", route="show", auth=False)

    def get_app_version_info(self, version: str | int, language: str = "en") -> JSON:
        return self.post(
            "v1/api/app/version/info",
            {"app": "Android", "version": str(version), "language": language},
            route="show",
            auth=False,
        )

    def get_ad_config(self, *, region: str = "", app_ver: str | int = 149, server: str | None = None) -> JSON:
        return self.get(
            "/v1/api/ad/config",
            params={
                "server": server or self.region.rest_url,
                "region": region,
                "app_os": "Android",
                "bundleid": BUNDLE_ID,
                "app_ver": app_ver,
            },
            auth=bool(self.token),
        )

    def get_system_uptime(self, *, language: str = "en", area: str | None = None) -> JSON:
        return self.get("v1/api/server/uptime/list", params={"language": language, "area": area or self.region.key}, route="developer", auth=False)

    def get_country(self) -> JSON:
        return self.get("v1/api/app/country", route="area", auth=False)

    def get_area_codes(self, language: str = "en") -> JSON:
        return self.get("v1/api/area/code", params={"language": language}, auth=False)

    def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        auth: bool = True,
        route: str = "formal",
        idcode: bool = False,
    ) -> JSON:
        return self.request("GET", path, params=params, auth=auth, route=route, idcode=idcode)

    def post(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        auth: bool = True,
        route: str = "formal",
        idcode: bool = False,
    ) -> JSON:
        return self.request("POST", path, json_body={} if body is None else body, auth=auth, route=route, idcode=idcode)

    def put(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        auth: bool = True,
        route: str = "formal",
        idcode: bool = False,
    ) -> JSON:
        return self.request("PUT", path, json_body={} if body is None else body, auth=auth, route=route, idcode=idcode)

    def delete(self, path: str, *, auth: bool = True, route: str = "formal", idcode: bool = False) -> JSON:
        return self.request("DELETE", path, auth=auth, route=route, idcode=idcode)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        auth: bool = True,
        route: str = "formal",
        idcode: bool = False,
        headers: dict[str, str] | None = None,
    ) -> JSON:
        request_headers = self.headers(auth=auth, idcode=idcode)
        if headers:
            request_headers.update(headers)
        response = self.session.request(
            method.upper(),
            self.url_for(path, route=route),
            params=_without_none(params or {}),
            json=json_body,
            headers=request_headers,
            timeout=self.timeout,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise XHomeAPIError(
                f"XHome HTTP {response.status_code} for {method.upper()} {path}",
                status_code=response.status_code,
                payload=_decode_response(response),
            ) from exc

        payload = _decode_response(response)
        if self.raise_api_errors:
            _raise_for_api_error(payload)
        return payload

    def headers(self, *, auth: bool = True, idcode: bool = False) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Connection": "close",
            "bundleid": BUNDLE_ID,
        }
        if idcode:
            headers["idcode"] = "1"
        if auth:
            headers["Token"] = self.require_token()
        return headers

    def require_token(self) -> str:
        if not self.token:
            raise XHomeAuthError("This API call requires login or an explicit token")
        return self.token

    def require_user_id(self) -> int:
        if self.user_id is None:
            raise XHomeAuthError("This API call requires login or an explicit user_id")
        return self.user_id

    def url_for(self, path: str, *, route: str = "formal") -> str:
        if path.startswith(("http://", "https://")):
            return path
        base_url = self.base_url if route == "formal" else ROUTE_BASE_URLS[route]
        return urljoin(base_url, path.lstrip("/"))


def unwrap_response(payload: JSON) -> JSON:
    """Return the useful inner payload when the API uses a known wrapper."""

    if not isinstance(payload, dict):
        return payload
    if "resultData" in payload:
        return payload["resultData"]
    if "data" in payload:
        return payload["data"]
    return payload


def _decode_response(response: requests.Response) -> JSON:
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return response.text


def _raise_for_api_error(payload: JSON) -> None:
    if not isinstance(payload, dict):
        return

    message = payload.get("message")
    if message in {"invalid token", "no token"}:
        raise XHomeAuthError(str(message))

    if "code" in payload and str(payload["code"]) not in {"0", "200"}:
        raise XHomeAPIError(str(message or f"XHome API returned code={payload['code']}"), payload=payload)

    if "resultStatus" in payload and str(payload["resultStatus"]) not in {"0", "200"}:
        raise XHomeAPIError(str(message or f"XHome API returned resultStatus={payload['resultStatus']}"), payload=payload)


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _optional_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _flag(value: bool | int) -> int:
    return int(value)


def _ids(value: str | int | list[int]) -> str:
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    return str(value)


def _push_token_body(
    push_token: str,
    *,
    push_platform: str,
    os_token: str,
    language: str,
    os_name: str,
    os_push_version: int,
    bundle_id: str,
    phone_model: str,
) -> dict[str, Any]:
    return {
        "push_token": push_token,
        "push_platform": push_platform,
        "language": language,
        "os_token": os_token,
        "os": os_name,
        "os_push_version": os_push_version,
        "bundleid": bundle_id,
        "phone_model": phone_model,
    }


def _without_none(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}
