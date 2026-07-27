from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

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

    def test_list_auth_uses_uuid_and_app_entry_query(self):
        session = FakeSession({"message": "ok"})
        client = XHomeClient(token="tok", session=session)
        client.list_auth("abc")

        _, kwargs = session.calls[0]
        self.assertEqual(kwargs["params"], {"uuid": "abc", "entry": "app"})

    def test_delete_auth_formats_list_ids(self):
        session = FakeSession({"message": "ok"})
        client = XHomeClient(token="tok", session=session)
        client.delete_auth("abc", [1, 2])

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

    def test_get_media_url_uses_uuid_key_for_uid(self):
        session = FakeSession({"message": "ok"})
        client = XHomeClient(token="tok", session=session)
        client.get_media_url("abc", "guid-1")

        args, kwargs = session.calls[0]
        self.assertEqual(args[1], "https://chniot.lancens.com:6448/v1/api/app/device/oss/list")
        self.assertEqual(kwargs["json"], {"uuid": "abc", "event_guid": "guid-1"})

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
