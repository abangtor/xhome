from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xhome import XHomeAPIError, XHomeAuthError, XHomeClient
from xhome.constants import API_KEY
from xhome.secrets import load_openclaw_auth_profile
from xhome.signing import login_sign, sha1_hex


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.content = json.dumps(payload).encode("utf-8") if payload is not None else b""
        self.text = self.content.decode("utf-8")

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(response=self)


class FakeSession:
    def __init__(self, response):
        self.response = response if isinstance(response, FakeResponse) else FakeResponse(response)
        self.calls = []

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response


class FakeSequenceSession:
    def __init__(self, responses):
        self.responses = [response if isinstance(response, FakeResponse) else FakeResponse(response) for response in responses]
        self.calls = []

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.responses.pop(0)


class SigningTests(unittest.TestCase):
    def test_sha1_hex(self):
        self.assertEqual(sha1_hex("abc"), "a9993e364706816aba3e25717850c26c9cd0d89d")

    def test_login_sign_uses_lowercase_joined_value(self):
        self.assertEqual(login_sign("User@Example.com", "PassWord", 1234567890), "2d7df0a27d91a9439b742ce8fd42a8ee4e26f3f2")


class ClientTests(unittest.TestCase):
    def test_login_sends_signed_body_and_stores_session(self):
        session = FakeSession({"id": 42, "token": "tok", "refresh_key": "ref"})
        client = XHomeClient(session=session)

        login = client.login("User@Example.com", "PassWord", timestamp=1234567890)

        self.assertEqual(login.user_id, 42)
        self.assertEqual(login.token, "tok")
        self.assertEqual(client.token, "tok")
        args, kwargs = session.calls[0]
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], "https://chniot.lancens.com:6448/v2/api/user/login/new")
        self.assertEqual(kwargs["json"]["apikey"], API_KEY)
        self.assertEqual(kwargs["json"]["sign"], "2d7df0a27d91a9439b742ce8fd42a8ee4e26f3f2")
        self.assertNotIn("Token", kwargs["headers"])

    def test_authenticated_headers_include_token(self):
        client = XHomeClient(token="tok")
        self.assertEqual(client.headers()["Token"], "tok")
        self.assertEqual(client.headers()["bundleid"], "com.lancens.wxdoorbell")

    def test_authenticated_call_without_token_raises(self):
        client = XHomeClient()
        with self.assertRaises(XHomeAuthError):
            client.list_all_devices()

    def test_url_for_region_alias(self):
        client = XHomeClient(region="eur")
        self.assertEqual(client.url_for("/v1/api/user/device"), "https://euriot.lancens.com:6448/v1/api/user/device")

    def test_get_device_detail_uses_uuid_query_key(self):
        session = FakeSession({"message": "ok"})
        client = XHomeClient(token="tok", session=session)
        client.get_device_detail("abc")

        _, kwargs = session.calls[0]
        self.assertEqual(kwargs["params"], {"uuid": "abc"})

    def test_list_devices_resilient_falls_back_from_all_device_400(self):
        session = FakeSequenceSession(
            [
                FakeResponse({"message": "bad request"}, status_code=400),
                FakeResponse([{"uid": "abc"}]),
            ]
        )
        client = XHomeClient(token="tok", session=session)

        self.assertEqual(client.list_devices_resilient(), [{"uid": "abc"}])
        self.assertEqual(session.calls[0][0][1], "https://chniot.lancens.com:6448/v1/api/user/all/device/list")
        self.assertEqual(session.calls[1][0][1], "https://chniot.lancens.com:6448/v1/api/user/device")

    def test_list_devices_resilient_keeps_non_400_errors_visible(self):
        session = FakeSequenceSession([FakeResponse({"message": "server error"}, status_code=500)])
        client = XHomeClient(token="tok", session=session)

        with self.assertRaises(XHomeAPIError):
            client.list_devices_resilient()
        self.assertEqual(len(session.calls), 1)

    def test_open_lock_body_uses_uuid_and_signed_timestamp(self):
        session = FakeSession({"message": "ok"})
        client = XHomeClient(token="tok", session=session)
        client.open_lock("abc", timestamp=123)

        _, kwargs = session.calls[0]
        self.assertEqual(kwargs["json"]["uuid"], "abc")
        self.assertEqual(kwargs["json"]["time"], 123)
        self.assertEqual(kwargs["json"]["sign"], "e70c099b2659269d70dc28450efab4e48cfd5509")

    def test_unlock_door_is_semantic_open_lock_alias(self):
        session = FakeSession({"message": "ok"})
        client = XHomeClient(token="tok", session=session)
        client.unlock_door("abc", timestamp=123)

        args, kwargs = session.calls[0]
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], "https://chniot.lancens.com:6448/v1/api/app/open/lock")
        self.assertEqual(kwargs["json"]["uuid"], "abc")
        self.assertEqual(kwargs["json"]["time"], 123)
        self.assertEqual(kwargs["json"]["sign"], "e70c099b2659269d70dc28450efab4e48cfd5509")

    def test_add_device_share_uses_idcode_header_and_uuid(self):
        session = FakeSession({"message": "ok"})
        client = XHomeClient(token="tok", session=session)
        client.add_device_share("abc")

        _, kwargs = session.calls[0]
        self.assertEqual(kwargs["headers"]["idcode"], "1")
        self.assertEqual(kwargs["json"], {"uuid": "abc", "entry": "app"})

    def test_lock_member_methods_use_member_endpoints(self):
        session = FakeSession({"message": "ok"})
        client = XHomeClient(token="tok", session=session)
        client.list_lock_members("abc")

        args, kwargs = session.calls[0]
        self.assertEqual(args[1], "https://chniot.lancens.com:6448/v1/api/users/device/member/list")
        self.assertEqual(kwargs["json"], {"uuid": "abc"})

        session = FakeSession({"message": "ok"})
        client = XHomeClient(token="tok", session=session)
        client.upsert_lock_member(
            "abc",
            remarks="Torsten",
            avatar="",
            lock_type=1,
            event_user_id=2,
            member_type=3,
            model=4,
            key_id=5,
        )

        args, kwargs = session.calls[0]
        self.assertEqual(args[1], "https://chniot.lancens.com:6448/v1/api/users/device/member/update/new")
        self.assertEqual(
            kwargs["json"],
            {
                "uuid": "abc",
                "remarks": "Torsten",
                "avatar": "",
                "lock_type": 1,
                "event_user_id": 2,
                "member_type": 3,
                "model": 4,
                "key_id": 5,
            },
        )

    def test_update_event_member_body(self):
        session = FakeSession({"message": "ok"})
        client = XHomeClient(token="tok", session=session)
        client.update_event_member("abc", event_user_id=2, member_type=3, remarks="Torsten")

        args, kwargs = session.calls[0]
        self.assertEqual(args[1], "https://chniot.lancens.com:6448/v1/api/users/event/member")
        self.assertEqual(kwargs["json"], {"uid": "abc", "event_user_id": 2, "member_type": 3, "remarks": "Torsten"})

    def test_list_auth_uses_uuid_and_app_entry_query(self):
        session = FakeSession({"message": "ok"})
        client = XHomeClient(token="tok", session=session)
        client.list_auth("abc")

        _, kwargs = session.calls[0]
        self.assertEqual(kwargs["params"], {"uuid": "abc", "entry": "app"})

    def test_add_temporary_password_raw_body(self):
        session = FakeSession({"message": "ok"})
        client = XHomeClient(token="tok", session=session)
        client.add_temporary_password_raw(
            "abc",
            name="Cleaner",
            data="encoded",
            rand_key="1234567890abcdef",
            begin_time=10,
            end_time=20,
            start_time=30,
            stop_time=40,
            total_times=1,
            week=127,
        )

        args, kwargs = session.calls[0]
        self.assertEqual(args[1], "https://chniot.lancens.com:6448/v1/api/device/iviews/auth/add")
        self.assertEqual(
            kwargs["json"],
            {
                "uuid": "abc",
                "entry": "app",
                "name": "Cleaner",
                "begin_time": 10,
                "end_time": 20,
                "start_time": 30,
                "stop_time": 40,
                "total_times": 1,
                "week": 127,
                "user_type": 2,
                "auth_type": 1,
                "data": "encoded",
                "rand_key": "1234567890abcdef",
            },
        )

    def test_ble_lock_device_methods(self):
        session = FakeSession({"message": "ok"})
        client = XHomeClient(token="tok", session=session)
        client.add_ble_lock_device(
            name="BLE Lock",
            code="123456",
            mac="aa:bb:cc",
            longitude=101.1,
            latitude=3.1,
            time_zone=8,
            iviews_func=7,
            blename="BLE-123",
        )

        args, kwargs = session.calls[0]
        self.assertEqual(args[1], "https://chniot.lancens.com:6448/v1/api/user/add/blelock/device")
        self.assertEqual(
            kwargs["json"],
            {
                "name": "BLE Lock",
                "code": "123456",
                "mac": "aa:bb:cc",
                "longitude": "101.1",
                "latitude": "3.1",
                "time_zone": 8,
                "iviews_func": 7,
                "blename": "BLE-123",
            },
        )

        session = FakeSession({"message": "ok"})
        client = XHomeClient(token="tok", session=session)
        client.new_ble_lock_device("abc", 9)

        args, kwargs = session.calls[0]
        self.assertEqual(args[1], "https://chniot.lancens.com:6448/v1/api/user/blelock/device/new")
        self.assertEqual(kwargs["json"], {"uuid": "abc", "model": 9})

    def test_delete_auth_formats_list_ids(self):
        session = FakeSession({"message": "ok"})
        client = XHomeClient(token="tok", session=session)
        client.delete_temporary_password("abc", [1, 2])

        _, kwargs = session.calls[0]
        self.assertEqual(kwargs["json"], {"uuid": "abc", "entry": "app", "ids": "1,2"})

    def test_get_app_lock_status_uses_sdk_query_key(self):
        session = FakeSession({"message": "ok"})
        client = XHomeClient(token="tok", session=session)
        client.get_app_lock_status("Brand", "Model", 33)

        _, kwargs = session.calls[0]
        self.assertEqual(kwargs["params"], {"brand": "brand", "model": "model", "sdk": 33})

    def test_change_gms_config_uses_signed_nested_body(self):
        session = FakeSession({"message": "ok"})
        client = XHomeClient(token="tok", user_id=42, session=session)
        client.change_gms_config("abc", 9, 123, timestamp=123)

        args, kwargs = session.calls[0]
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], "https://chniot.lancens.com:6448/v1/api/app/device/gms/change")
        self.assertEqual(kwargs["json"]["apikey"], API_KEY)
        self.assertEqual(kwargs["json"]["sign"], "11bb31f00c2fd67ad74d4349e8ed3cc1dd0c4511")
        self.assertEqual(kwargs["json"]["time"], 123)
        self.assertEqual(kwargs["json"]["data"], {"uuid": "abc", "model": 9})
        self.assertEqual(kwargs["json"]["change_data"], {"uuid": "abc", "gms": 123})

    def test_change_gms_config_requires_user_id(self):
        client = XHomeClient(token="tok")

        with self.assertRaises(XHomeAuthError):
            client.change_gms_config("abc", 9, 123, timestamp=123)

    def test_app_safe_password_body(self):
        session = FakeSession({"message": "ok"})
        client = XHomeClient(token="tok", session=session)
        client.set_app_safe_password("account-pass", "safe-pass")

        args, kwargs = session.calls[0]
        self.assertEqual(args[1], "https://chniot.lancens.com:6448/v1/api/user/app/safe")
        self.assertEqual(kwargs["json"], {"password": "account-pass", "safe_password": "safe-pass", "entry": "app"})

    def test_app_safe_lock_body(self):
        session = FakeSession({"message": "ok"})
        client = XHomeClient(token="tok", session=session)
        client.set_app_safe_lock("safe-pass")

        args, kwargs = session.calls[0]
        self.assertEqual(args[1], "https://chniot.lancens.com:6448/v1/api/app/safe/lock")
        self.assertEqual(kwargs["json"], {"safe_password": "safe-pass", "entry": "app"})

    def test_add_temporary_password_encodes_plain_password(self):
        session = FakeSession({"message": "ok"})
        client = XHomeClient(token="tok", session=session)

        with patch("xhome.client.encode_temporary_password", return_value=("encoded", "rand")):
            client.add_temporary_password("abcd1234567890abcdef", name="Guest", password="246810")

        args, kwargs = session.calls[0]
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], "https://chniot.lancens.com:6448/v1/api/device/iviews/auth/add")
        self.assertEqual(kwargs["json"]["uuid"], "abcd1234567890abcdef")
        self.assertEqual(kwargs["json"]["name"], "Guest")
        self.assertEqual(kwargs["json"]["data"], "encoded")
        self.assertEqual(kwargs["json"]["rand_key"], "rand")

    def test_register_push_tokens_posts_call_and_message_tokens(self):
        session = FakeSession({"message": "ok"})
        client = XHomeClient(token="tok", session=session)

        client.register_push_tokens("push-token", phone_model="xhome-test")

        self.assertEqual(len(session.calls), 2)
        self.assertEqual(session.calls[0][0][1], "https://chniot.lancens.com:6448/v1/api/user/token")
        self.assertEqual(session.calls[1][0][1], "https://chniot.lancens.com:6448/v1/api/user/message/token")
        for _, kwargs in session.calls:
            self.assertEqual(
                kwargs["json"],
                {
                    "push_token": "push-token",
                    "push_platform": "FCM",
                    "language": "en",
                    "os_token": "",
                    "os": "ANDROID",
                    "os_push_version": 1,
                    "bundleid": "com.lancens.wxdoorbell",
                    "phone_model": "xhome-test",
                },
            )

    def test_get_media_url_uses_uuid_key_for_uid(self):
        session = FakeSession({"message": "ok"})
        client = XHomeClient(token="tok", session=session)
        client.get_media_url("abc", "guid-1")

        args, kwargs = session.calls[0]
        self.assertEqual(args[1], "https://chniot.lancens.com:6448/v1/api/app/device/oss/list")
        self.assertEqual(kwargs["json"], {"uuid": "abc", "event_guid": "guid-1"})

    def test_device_setting_setter_bodies(self):
        cases = (
            (
                lambda client: client.set_screen_light_timeout("abc", 30),
                "https://chniot.lancens.com:6448/v1/api/device/screen/light",
                {"uid": "abc", "screenon_timeout": 30},
            ),
            (
                lambda client: client.set_battery_display("abc", True),
                "https://chniot.lancens.com:6448/v1/api/device/battery/status",
                {"uuid": "abc", "bat_display_en": 1},
            ),
            (
                lambda client: client.set_wet_play("abc", False),
                "https://chniot.lancens.com:6448/v1/api/device/wet_play/status",
                {"uuid": "abc", "wet_play": 0},
            ),
            (
                lambda client: client.set_call_screen("abc", True),
                "https://chniot.lancens.com:6448/v1/api/device/call/screen/status",
                {"uuid": "abc", "call_screen_on": 1},
            ),
            (
                lambda client: client.set_standby_mode("abc", 1),
                "https://chniot.lancens.com:6448/v1/api/device/standby_mode/status",
                {"uid": "abc", "standby_mode": 1},
            ),
            (
                lambda client: client.set_target_ev("abc", 42),
                "https://chniot.lancens.com:6448/v1/api/device/target/ev",
                {"uid": "abc", "target_ev": 42},
            ),
            (
                lambda client: client.set_device_unlock_limit("abc", 0),
                "https://chniot.lancens.com:6448/v1/api/device/unlock/status",
                {"uuid": "abc", "unlock_limit": 0},
            ),
            (
                lambda client: client.set_remote_unlock_limit("abc", 1),
                "https://chniot.lancens.com:6448/v1/api/device/unlock/status",
                {"uuid": "abc", "unlock_limit": 1},
            ),
            (
                lambda client: client.set_notify_control(123, 65),
                "https://chniot.lancens.com:6448/v1/api/device/notify_ctrl/123",
                {"notify_ctrl": 65},
            ),
        )

        for call, url, body in cases:
            with self.subTest(url=url):
                session = FakeSession({"message": "ok"})
                client = XHomeClient(token="tok", session=session)
                call(client)

                args, kwargs = session.calls[0]
                self.assertEqual(args[0], "POST")
                self.assertEqual(args[1], url)
                self.assertEqual(kwargs["json"], body)

    def test_result_status_200_is_success(self):
        session = FakeSession({"message": "success", "resultStatus": 200, "resultData": {"ok": True}})
        client = XHomeClient(token="tok", session=session)

        self.assertEqual(client.get_current_user()["resultStatus"], 200)

    def test_non_success_result_status_raises(self):
        session = FakeSession({"message": "no func", "resultStatus": 404})
        client = XHomeClient(token="tok", session=session)

        with self.assertRaises(XHomeAPIError):
            client.get_current_user()

    def test_code_200_is_success(self):
        session = FakeSession({"message": "success", "code": 200, "result": []})
        client = XHomeClient(token="tok", session=session)

        self.assertEqual(client.get_current_user()["code"], 200)

    def test_non_success_code_raises(self):
        session = FakeSession({"message": "bad", "code": 500})
        client = XHomeClient(token="tok", session=session)

        with self.assertRaises(XHomeAPIError):
            client.get_current_user()


class OpenClawSecretsTests(unittest.TestCase):
    def test_load_openclaw_auth_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "secrets.json"
            path.write_text(json.dumps({"authProfiles": {"xhome": {"username": "u", "password": "p", "region": "china"}}}))

            self.assertEqual(
                load_openclaw_auth_profile("xhome", secrets_file=path),
                {"username": "u", "password": "p", "region": "china"},
            )

    def test_missing_openclaw_auth_profile_returns_empty_dict(self):
        self.assertEqual(load_openclaw_auth_profile("missing", secrets_file="/does/not/exist"), {})


if __name__ == "__main__":
    unittest.main()
