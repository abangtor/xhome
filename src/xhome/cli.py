"""Command-line interface for xhome-api."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from typing import Any

from .client import XHomeClient
from .exceptions import XHomeError
from .secrets import load_openclaw_auth_profile


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.func(args)
    except XHomeError as exc:
        print(f"xhome: {exc}", file=sys.stderr)
        return 1
    if result is not None:
        print_json(result)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="XHome/Lancens API CLI")
    parser.add_argument("--region", default=os.getenv("XHOME_REGION"))
    parser.add_argument("--base-url", default=os.getenv("XHOME_BASE_URL"))
    parser.add_argument("--token", default=os.getenv("XHOME_TOKEN"))
    parser.add_argument("--user-id", default=os.getenv("XHOME_USER_ID"))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("XHOME_TIMEOUT", "30")))
    parser.add_argument("--profile", default=os.getenv("XHOME_PROFILE", "xhome"), help="OpenClaw auth profile name")
    parser.add_argument("--secrets-file", default=os.getenv("OPENCLAW_SECRETS_FILE"), help="OpenClaw secrets file path")
    parser.add_argument("--no-secrets", action="store_true", help="Do not read OpenClaw secrets.json")

    subparsers = parser.add_subparsers(required=True)

    login = subparsers.add_parser("login", help="Log in with username/password")
    login.add_argument("username", nargs="?", default=os.getenv("XHOME_USERNAME"))
    login.add_argument("--password", default=os.getenv("XHOME_PASSWORD"))
    login.add_argument("--show-token", action="store_true", help="Print the full token instead of a redacted value")
    login.set_defaults(func=cmd_login)

    user = subparsers.add_parser("user", help="Get current user profile")
    user.set_defaults(func=lambda args: logged_in_client(args).get_current_user())

    devices = subparsers.add_parser("devices", help="List devices")
    devices.add_argument("--main", action="store_true", help="Use the main-device endpoint instead of all-device list")
    devices.set_defaults(func=cmd_devices)

    detail = subparsers.add_parser("detail", help="Get one device detail record")
    detail.add_argument("uuid")
    detail.set_defaults(func=lambda args: logged_in_client(args).get_device_detail(args.uuid))

    firmware = subparsers.add_parser("firmware", help="List firmware for a device")
    firmware.add_argument("uuid")
    firmware.add_argument("--language", default="en")
    firmware.set_defaults(func=lambda args: logged_in_client(args).get_firmware(args.uuid, args.language))

    logos = subparsers.add_parser("logos", help="List device logos by numeric device type")
    logos.add_argument("device_type", type=int)
    logos.set_defaults(func=lambda args: logged_in_client(args).get_device_logos(args.device_type))

    networks = subparsers.add_parser("networks", help="List supported networks/products")
    networks.add_argument("--language", default="en")
    networks.set_defaults(func=lambda args: logged_in_client(args).list_networks(args.language))

    token = subparsers.add_parser("token", help="Get native live token metadata for a device")
    group = token.add_mutually_exclusive_group(required=True)
    group.add_argument("--id", type=int, dest="device_id")
    group.add_argument("--uid")
    token.set_defaults(func=cmd_token)

    online = subparsers.add_parser("online", help="Get device online status")
    online.add_argument("uid")
    online.add_argument("--online-type", default="1")
    online.set_defaults(func=cmd_online)

    record_time = subparsers.add_parser("record-time", help="Fetch device record-time metadata")
    record_time.add_argument("uuid")
    record_time.set_defaults(func=lambda args: logged_in_client(args).get_record_time(args.uuid))

    new_events = subparsers.add_parser("new-events", help="Fetch new device events")
    new_events.add_argument("--uuid", required=True)
    new_events.add_argument("--type", required=True, dest="device_type")
    new_events.set_defaults(func=cmd_new_events)

    events = subparsers.add_parser("events", help="Fetch events by timestamp window")
    events.add_argument("--raw-time", required=True, type=int)
    events.add_argument("--start-time", required=True, type=int)
    events.add_argument("--end-time", required=True, type=int)
    events.add_argument("--page", type=int, default=0)
    events.add_argument("--page-number", type=int, default=20)
    events.set_defaults(func=cmd_events)

    event_totals = subparsers.add_parser("event-totals", help="Fetch event totals")
    event_totals.add_argument("--time-zone", required=True)
    event_totals.add_argument("--time-zone-offset", required=True)
    event_totals.set_defaults(func=lambda args: logged_in_client(args).get_event_totals(time_zone=args.time_zone, time_zone_offset=args.time_zone_offset))

    media = subparsers.add_parser("media", help="Get OSS media URLs for an event")
    media.add_argument("--uuid", required=True)
    media.add_argument("--event-guid", required=True)
    media.set_defaults(func=cmd_media)

    set_name = subparsers.add_parser("set-name", help="Rename a bound device")
    set_name.add_argument("uid")
    set_name.add_argument("name")
    set_name.set_defaults(func=lambda args: logged_in_client(args).set_device_name(args.uid, args.name))

    screen_light = subparsers.add_parser("screen-light", help="Get or set screen-light timeout")
    screen_light.add_argument("uid")
    screen_light.add_argument("--set", type=int, dest="timeout_seconds")
    screen_light.set_defaults(func=cmd_screen_light)

    set_notify = subparsers.add_parser("set-notify", help="Set device notification control")
    set_notify.add_argument("device_id", type=int)
    set_notify.add_argument("notify_ctrl", type=int)
    set_notify.set_defaults(func=lambda args: logged_in_client(args).set_notify_control(args.device_id, args.notify_ctrl))

    set_share_notify = subparsers.add_parser("set-share-notify", help="Set shared-device notification control")
    set_share_notify.add_argument("uid")
    set_share_notify.add_argument("notify_ctrl", type=int)
    set_share_notify.set_defaults(func=lambda args: logged_in_client(args).set_share_notify_control(args.uid, args.notify_ctrl))

    battery_display = subparsers.add_parser("battery-display", help="Set battery display flag")
    battery_display.add_argument("uuid")
    battery_display.add_argument("enabled")
    battery_display.set_defaults(func=lambda args: logged_in_client(args).set_battery_display(args.uuid, parse_bool(args.enabled)))

    wet_play = subparsers.add_parser("wet-play", help="Set wet-play flag")
    wet_play.add_argument("uuid")
    wet_play.add_argument("enabled")
    wet_play.set_defaults(func=lambda args: logged_in_client(args).set_wet_play(args.uuid, parse_bool(args.enabled)))

    call_screen = subparsers.add_parser("call-screen", help="Set call-screen flag")
    call_screen.add_argument("uuid")
    call_screen.add_argument("enabled")
    call_screen.set_defaults(func=lambda args: logged_in_client(args).set_call_screen(args.uuid, parse_bool(args.enabled)))

    standby = subparsers.add_parser("standby", help="Set standby mode")
    standby.add_argument("uid")
    standby.add_argument("mode", type=int)
    standby.set_defaults(func=lambda args: logged_in_client(args).set_standby_mode(args.uid, args.mode))

    target_ev = subparsers.add_parser("target-ev", help="Set target exposure value")
    target_ev.add_argument("uid")
    target_ev.add_argument("value", type=int)
    target_ev.set_defaults(func=lambda args: logged_in_client(args).set_target_ev(args.uid, args.value))

    app_lock_status = subparsers.add_parser("app-lock-status", help="Get app lock/unlock status")
    app_lock_status.add_argument("--brand", default="python")
    app_lock_status.add_argument("--model", default="xhome-api")
    app_lock_status.add_argument("--sdk", type=int, default=35)
    app_lock_status.set_defaults(func=lambda args: logged_in_client(args).get_app_lock_status(args.brand, args.model, args.sdk))

    unlock_type = subparsers.add_parser("unlock-type", help="Set account unlock type")
    unlock_type.add_argument("value", type=int)
    unlock_type.set_defaults(func=lambda args: logged_in_client(args).set_unlock_type(args.value))

    unlock_limit = subparsers.add_parser("unlock-limit", help="Set per-device unlock limit")
    unlock_limit.add_argument("uuid")
    unlock_limit.add_argument("value", type=int)
    unlock_limit.set_defaults(func=lambda args: logged_in_client(args).set_device_unlock_limit(args.uuid, args.value))

    open_lock = subparsers.add_parser("open-lock", help="Open/unlock a device via the cloud API")
    open_lock.add_argument("uuid")
    open_lock.add_argument("--yes", action="store_true", help="Confirm that you really want to trigger the lock")
    open_lock.set_defaults(func=cmd_unlock, unlock_method="open_lock")

    unlock_door = subparsers.add_parser("unlock-door", help="Unlock a door device via the cloud API")
    unlock_door.add_argument("uuid")
    unlock_door.add_argument("--yes", action="store_true", help="Confirm that you really want to trigger the door unlock")
    unlock_door.set_defaults(func=cmd_unlock, unlock_method="unlock_door")

    friends = subparsers.add_parser("friends", help="List share friends")
    friends.set_defaults(func=lambda args: logged_in_client(args).list_share_friends())

    search_user = subparsers.add_parser("search-user", help="Search XHome user by username")
    search_user.add_argument("username")
    search_user.set_defaults(func=lambda args: logged_in_client(args).search_user(args.username))

    shared_devices = subparsers.add_parser("shared-devices", help="List devices shared with/by a uid")
    shared_devices.add_argument("uid")
    shared_devices.set_defaults(func=lambda args: logged_in_client(args).list_shared_devices(args.uid))

    group_shares = subparsers.add_parser("group-shares", help="List or get group shares")
    group_shares.add_argument("--group")
    group_shares.set_defaults(func=cmd_group_shares)

    members = subparsers.add_parser("members", help="List lock/device members")
    members.add_argument("--uuid")
    members.set_defaults(func=cmd_members)

    member_upsert = subparsers.add_parser("member-upsert", help="Add or update a lock/device member")
    member_upsert.add_argument("uuid")
    member_upsert.add_argument("--remarks", required=True)
    member_upsert.add_argument("--avatar", default="")
    member_upsert.add_argument("--lock-type", type=int, default=0)
    member_upsert.add_argument("--event-user-id", type=int, default=0)
    member_upsert.add_argument("--member-type", type=int, default=0)
    member_upsert.add_argument("--model", type=int, default=0)
    member_upsert.add_argument("--key-id", type=int)
    member_upsert.set_defaults(func=cmd_member_upsert)

    event_member = subparsers.add_parser("event-member", help="Update the member label for an event user")
    event_member.add_argument("uid")
    event_member.add_argument("event_user_id", type=int)
    event_member.add_argument("member_type", type=int)
    event_member.add_argument("remarks")
    event_member.set_defaults(func=cmd_event_member)

    auth_list = subparsers.add_parser("auth-list", help="List temporary-password/auth entries for a device")
    auth_list.add_argument("uuid")
    auth_list.set_defaults(func=lambda args: logged_in_client(args).list_auth(args.uuid))

    auth_all = subparsers.add_parser("auth-all", help="List all auth entries")
    auth_all.set_defaults(func=lambda args: logged_in_client(args).list_all_auth())

    auth_add = subparsers.add_parser("auth-add", help="Add a temporary-password/auth entry")
    auth_add.add_argument("uuid")
    auth_add.add_argument("--name", required=True)
    auth_add.add_argument("--password", required=True)
    auth_add.add_argument("--begin-time", type=int, default=0)
    auth_add.add_argument("--end-time", type=int, default=0)
    auth_add.add_argument("--start-time", type=int, default=0)
    auth_add.add_argument("--stop-time", type=int, default=0)
    auth_add.add_argument("--total-times", type=int, default=0)
    auth_add.add_argument("--week", type=int, default=0)
    auth_add.add_argument("--user-type", type=int, default=2)
    auth_add.add_argument("--auth-type", type=int, default=1)
    auth_add.add_argument("--entry", default="app")
    auth_add.add_argument("--yes", action="store_true")
    auth_add.set_defaults(func=cmd_auth_add)

    auth_add_raw = subparsers.add_parser("auth-add-raw", help="Submit a pre-encoded temporary-password/auth entry")
    auth_add_raw.add_argument("uuid")
    auth_add_raw.add_argument("--name", required=True)
    auth_add_raw.add_argument("--data", required=True, help="Native IVIEWSPassword-encoded data blob")
    auth_add_raw.add_argument("--rand-key", required=True)
    auth_add_raw.add_argument("--begin-time", type=int, default=0)
    auth_add_raw.add_argument("--end-time", type=int, default=0)
    auth_add_raw.add_argument("--start-time", type=int, default=0)
    auth_add_raw.add_argument("--stop-time", type=int, default=0)
    auth_add_raw.add_argument("--total-times", type=int, default=0)
    auth_add_raw.add_argument("--week", type=int, default=0)
    auth_add_raw.add_argument("--user-type", type=int, default=2)
    auth_add_raw.add_argument("--auth-type", type=int, default=1)
    auth_add_raw.add_argument("--entry", default="app")
    auth_add_raw.add_argument("--yes", action="store_true")
    auth_add_raw.set_defaults(func=cmd_auth_add_raw)

    auth_rename = subparsers.add_parser("auth-rename", help="Rename auth entry IDs")
    auth_rename.add_argument("uuid")
    auth_rename.add_argument("ids")
    auth_rename.add_argument("name")
    auth_rename.set_defaults(func=lambda args: logged_in_client(args).update_auth_name(args.uuid, args.ids, args.name))

    auth_delete = subparsers.add_parser("auth-delete", help="Delete auth entry IDs")
    auth_delete.add_argument("uuid")
    auth_delete.add_argument("ids")
    auth_delete.add_argument("--yes", action="store_true")
    auth_delete.set_defaults(func=cmd_auth_delete)

    ble_lock_add = subparsers.add_parser("ble-lock-add", help="Add a BLE lock device using raw API fields")
    ble_lock_add.add_argument("--name", required=True)
    ble_lock_add.add_argument("--code", required=True)
    ble_lock_add.add_argument("--mac", required=True)
    ble_lock_add.add_argument("--longitude", required=True)
    ble_lock_add.add_argument("--latitude", required=True)
    ble_lock_add.add_argument("--time-zone", type=int, required=True)
    ble_lock_add.add_argument("--iviews-func", type=int, required=True)
    ble_lock_add.add_argument("--blename", required=True)
    ble_lock_add.add_argument("--yes", action="store_true")
    ble_lock_add.set_defaults(func=cmd_ble_lock_add)

    ble_lock_new = subparsers.add_parser("ble-lock-new", help="Create a new BLE lock device record")
    ble_lock_new.add_argument("uuid")
    ble_lock_new.add_argument("model", type=int)
    ble_lock_new.add_argument("--yes", action="store_true")
    ble_lock_new.set_defaults(func=cmd_ble_lock_new)

    gms_list = subparsers.add_parser("gms-list", help="Get GMS/device config list for a device")
    gms_list.add_argument("uuid")
    gms_list.add_argument("model", type=int)
    gms_list.add_argument("--language", default="en")
    gms_list.set_defaults(func=lambda args: logged_in_client(args).get_gms_list(args.uuid, args.model, args.language))

    gms_all = subparsers.add_parser("gms-all", help="Get all account GMS/device config entries")
    gms_all.add_argument("--language", default="en")
    gms_all.set_defaults(func=lambda args: logged_in_client(args).get_all_gms_list(args.language))

    gms_info = subparsers.add_parser("gms-info", help="Get one GMS/device config model entry")
    gms_info.add_argument("model", type=int)
    gms_info.set_defaults(func=lambda args: logged_in_client(args).get_one_gms_info(args.model))

    gms_change = subparsers.add_parser("gms-change", help="Change a device GMS/config value")
    gms_change.add_argument("uuid")
    gms_change.add_argument("model", type=int)
    gms_change.add_argument("gms", type=int)
    gms_change.add_argument("--target-uuid")
    gms_change.add_argument("--yes", action="store_true")
    gms_change.set_defaults(func=cmd_gms_change)

    app_safe_set = subparsers.add_parser("app-safe-set", help="Set/update app safe password")
    app_safe_set.add_argument("--password", help="Current account/app password; prompts if omitted")
    app_safe_set.add_argument("--safe-password", help="New safe password; prompts if omitted")
    app_safe_set.add_argument("--entry", default="app")
    app_safe_set.add_argument("--yes", action="store_true")
    app_safe_set.set_defaults(func=cmd_app_safe_set)

    app_safe_lock = subparsers.add_parser("app-safe-lock", help="Set app safe-lock password state")
    app_safe_lock.add_argument("--safe-password", help="Safe password; prompts if omitted")
    app_safe_lock.add_argument("--entry", default="app")
    app_safe_lock.add_argument("--yes", action="store_true")
    app_safe_lock.set_defaults(func=cmd_app_safe_lock)

    show_status = subparsers.add_parser("show-status", help="Get app show status")
    show_status.set_defaults(func=lambda args: make_client(args).get_app_show_status())

    country = subparsers.add_parser("country", help="Get app country lookup")
    country.set_defaults(func=lambda args: make_client(args).get_country())

    area_codes = subparsers.add_parser("area-codes", help="Get area/country phone codes")
    area_codes.add_argument("--language", default="en")
    area_codes.set_defaults(func=lambda args: make_client(args).get_area_codes(args.language))

    uptime = subparsers.add_parser("uptime", help="Get system uptime/notice list")
    uptime.add_argument("--language", default="en")
    uptime.add_argument("--area")
    uptime.set_defaults(func=lambda args: make_client(args).get_system_uptime(language=args.language, area=args.area))

    raw = subparsers.add_parser("raw", help="Make a raw API request for exploration")
    raw.add_argument("method")
    raw.add_argument("path")
    raw.add_argument("--body", help="JSON request body")
    raw.add_argument("--query", help="JSON query object")
    raw.add_argument("--no-auth", action="store_true")
    raw.add_argument("--route", default="formal")
    raw.set_defaults(func=cmd_raw)

    return parser


def cmd_login(args: argparse.Namespace) -> dict[str, Any]:
    profile = stored_profile(args)
    username = args.username or profile.get("username")
    if not username:
        raise SystemExit("Missing username. Pass it as an argument, set XHOME_USERNAME, or store authProfiles.xhome.")
    password = args.password or profile.get("password") or getpass.getpass("XHome password: ")
    session = make_client(args).login(username, password)
    token = session.token if args.show_token else redact(session.token)
    return {
        "user_id": session.user_id,
        "token": token,
        "refresh_key": session.refresh_key,
        "logout_status": session.logout_status,
        "server_time": session.server_time,
        "token_redacted": not args.show_token,
    }


def cmd_devices(args: argparse.Namespace) -> Any:
    client = logged_in_client(args)
    return client.list_devices() if args.main else client.list_all_devices()


def cmd_token(args: argparse.Namespace) -> Any:
    return logged_in_client(args).get_device_token(device_id=args.device_id, uid=args.uid)


def cmd_online(args: argparse.Namespace) -> Any:
    return make_client(args).get_online_status(args.uid, args.online_type)


def cmd_events(args: argparse.Namespace) -> Any:
    return logged_in_client(args).get_events_by_time(
        raw_time=args.raw_time,
        start_time=args.start_time,
        end_time=args.end_time,
        page_number=args.page_number,
        page=args.page,
    )


def cmd_new_events(args: argparse.Namespace) -> Any:
    return logged_in_client(args).get_new_device_events(args.uuid, args.device_type)


def cmd_media(args: argparse.Namespace) -> Any:
    return logged_in_client(args).get_media_url(args.uuid, args.event_guid)


def cmd_screen_light(args: argparse.Namespace) -> Any:
    client = logged_in_client(args)
    if args.timeout_seconds is None:
        return client.get_screen_light_config(args.uid)
    return client.set_screen_light_timeout(args.uid, args.timeout_seconds)


def cmd_unlock(args: argparse.Namespace) -> Any:
    if not args.yes:
        raise SystemExit("Refusing to unlock without --yes")
    client = logged_in_client(args)
    method = getattr(client, args.unlock_method)
    return method(args.uuid)


def cmd_group_shares(args: argparse.Namespace) -> Any:
    client = logged_in_client(args)
    if args.group:
        return client.get_group_share(args.group)
    return client.list_group_shares()


def cmd_members(args: argparse.Namespace) -> Any:
    client = logged_in_client(args)
    if args.uuid:
        return client.list_device_members(args.uuid)
    return client.list_all_device_members()


def cmd_member_upsert(args: argparse.Namespace) -> Any:
    return logged_in_client(args).upsert_lock_member(
        args.uuid,
        remarks=args.remarks,
        avatar=args.avatar,
        lock_type=args.lock_type,
        event_user_id=args.event_user_id,
        member_type=args.member_type,
        model=args.model,
        key_id=args.key_id,
    )


def cmd_event_member(args: argparse.Namespace) -> Any:
    return logged_in_client(args).update_event_member(
        args.uid,
        args.event_user_id,
        args.member_type,
        args.remarks,
    )


def cmd_auth_add(args: argparse.Namespace) -> Any:
    if not args.yes:
        raise SystemExit("Refusing to add a temporary-password/auth entry without --yes")
    return logged_in_client(args).add_temporary_password(
        args.uuid,
        name=args.name,
        password=args.password,
        begin_time=args.begin_time,
        end_time=args.end_time,
        start_time=args.start_time,
        stop_time=args.stop_time,
        total_times=args.total_times,
        week=args.week,
        user_type=args.user_type,
        auth_type=args.auth_type,
        entry=args.entry,
    )


def cmd_auth_add_raw(args: argparse.Namespace) -> Any:
    if not args.yes:
        raise SystemExit("Refusing to add a temporary-password/auth entry without --yes")
    return logged_in_client(args).add_temporary_password_raw(
        args.uuid,
        name=args.name,
        data=args.data,
        rand_key=args.rand_key,
        begin_time=args.begin_time,
        end_time=args.end_time,
        start_time=args.start_time,
        stop_time=args.stop_time,
        total_times=args.total_times,
        week=args.week,
        user_type=args.user_type,
        auth_type=args.auth_type,
        entry=args.entry,
    )


def cmd_auth_delete(args: argparse.Namespace) -> Any:
    if not args.yes:
        raise SystemExit("Refusing to delete auth entries without --yes")
    return logged_in_client(args).delete_auth(args.uuid, args.ids)


def cmd_ble_lock_add(args: argparse.Namespace) -> Any:
    if not args.yes:
        raise SystemExit("Refusing to add a BLE lock device without --yes")
    return logged_in_client(args).add_ble_lock_device(
        name=args.name,
        code=args.code,
        mac=args.mac,
        longitude=args.longitude,
        latitude=args.latitude,
        time_zone=args.time_zone,
        iviews_func=args.iviews_func,
        blename=args.blename,
    )


def cmd_ble_lock_new(args: argparse.Namespace) -> Any:
    if not args.yes:
        raise SystemExit("Refusing to create a BLE lock device record without --yes")
    return logged_in_client(args).new_ble_lock_device(args.uuid, args.model)


def cmd_gms_change(args: argparse.Namespace) -> Any:
    if not args.yes:
        raise SystemExit("Refusing to change GMS/device config without --yes")
    return logged_in_client(args).change_gms_config(args.uuid, args.model, args.gms, target_uuid=args.target_uuid)


def cmd_app_safe_set(args: argparse.Namespace) -> Any:
    if not args.yes:
        raise SystemExit("Refusing to set app safe password without --yes")
    password = args.password or getpass.getpass("XHome current password: ")
    safe_password = args.safe_password or getpass.getpass("XHome safe password: ")
    return logged_in_client(args).set_app_safe_password(password, safe_password, entry=args.entry)


def cmd_app_safe_lock(args: argparse.Namespace) -> Any:
    if not args.yes:
        raise SystemExit("Refusing to set app safe lock without --yes")
    safe_password = args.safe_password or getpass.getpass("XHome safe password: ")
    return logged_in_client(args).set_app_safe_lock(safe_password, entry=args.entry)


def cmd_raw(args: argparse.Namespace) -> Any:
    client = logged_in_client(args) if not args.no_auth else make_client(args)
    body = json.loads(args.body) if args.body else None
    query = json.loads(args.query) if args.query else None
    return client.request(
        args.method,
        args.path,
        params=query,
        json_body=body,
        auth=not args.no_auth,
        route=args.route,
    )


def make_client(args: argparse.Namespace) -> XHomeClient:
    profile = stored_profile(args)
    return XHomeClient(
        region=args.region or profile.get("region") or "china",
        base_url=args.base_url,
        token=args.token or profile.get("token"),
        user_id=args.user_id or profile.get("user_id"),
        timeout=args.timeout,
    )


def logged_in_client(args: argparse.Namespace) -> XHomeClient:
    profile = stored_profile(args)
    client = make_client(args)
    if client.token:
        return client
    username = os.getenv("XHOME_USERNAME") or profile.get("username")
    password = os.getenv("XHOME_PASSWORD") or profile.get("password")
    if not username or not password:
        raise SystemExit("Set XHOME_TOKEN, both XHOME_USERNAME and XHOME_PASSWORD, or authProfiles.xhome in OpenClaw secrets.")
    client.login(username, password)
    return client


def stored_profile(args: argparse.Namespace) -> dict[str, Any]:
    if args.no_secrets:
        return {}
    return load_openclaw_auth_profile(args.profile, secrets_file=args.secrets_file)


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False))


def parse_bool(value: str) -> int:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on", "enable", "enabled"}:
        return 1
    if normalized in {"0", "false", "no", "off", "disable", "disabled"}:
        return 0
    raise argparse.ArgumentTypeError(f"Expected boolean-ish value, got {value!r}")


def redact(value: str, *, prefix: int = 6, suffix: int = 4) -> str:
    if len(value) <= prefix + suffix:
        return "***"
    return f"{value[:prefix]}...{value[-suffix:]}"


if __name__ == "__main__":
    raise SystemExit(main())
