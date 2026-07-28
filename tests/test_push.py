from __future__ import annotations

import base64
import json
import ssl
import struct
import unittest

from xhome.push import (
    PUSH_CMD_EVENT,
    PUSH_CMD_TOKEN,
    XHomePushFrame,
    build_push_register_info,
    decode_push_header,
    encode_push_frame,
    _ssl_context,
    parse_push_event,
    parse_push_frame,
    parse_push_token,
)


class PushProtocolTests(unittest.TestCase):
    def test_build_push_register_info_is_stable_and_app_compatible(self):
        first = build_push_register_info(42, model="xhome-api", brand="HomeAssistant")
        second = build_push_register_info(42, model="xhome-api", brand="HomeAssistant")

        self.assertEqual(first, second)
        self.assertEqual(first["type"], "xhome-api")
        self.assertEqual(first["brand"], "HomeAssistant")
        self.assertEqual(first["bundle_id"], "com.lancens.wxdoorbell")
        self.assertTrue(first["imei"].endswith("_42"))
        self.assertTrue(first["imsi"].endswith("_42"))

    def test_frame_encoding_is_little_endian_command_length_payload(self):
        frame = encode_push_frame(3, b"hello")

        self.assertEqual(frame[:8], struct.pack("<ii", 3, 5))
        self.assertEqual(frame[8:], b"hello")
        self.assertEqual(decode_push_header(frame[:8]), (3, 5))

    def test_push_tls_context_is_app_compatible(self):
        context = _ssl_context(False)

        self.assertFalse(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_NONE)
        if hasattr(ssl, "TLSVersion"):
            self.assertEqual(context.maximum_version, ssl.TLSVersion.TLSv1_2)

    def test_parse_push_token(self):
        payload = b'{"token":"push-token"}'

        self.assertEqual(parse_push_token(payload), "push-token")
        message = parse_push_frame(XHomePushFrame(PUSH_CMD_TOKEN, payload))
        self.assertEqual(message.kind, "token")
        self.assertEqual(message.token, "push-token")

    def test_parse_push_event_matches_android_push_info_fields(self):
        aps2 = base64.b64encode(
            json.dumps(
                {
                    "alert": "Someone rang",
                    "sound": "ding.mp3",
                    "message": "Doorbell",
                    "name": "MainDoor",
                }
            ).encode("utf-8")
        ).decode("ascii")
        func = base64.b64encode(b'{"ts":1234567890}').decode("ascii")
        info = base64.b64encode(b'{"orientation":90}').decode("ascii")
        payload = json.dumps(
            {
                "uid": "abc",
                "device": "MainDoor",
                "type": "1",
                "action": "call",
                "guid": "guid-1",
                "other": "https://example.test/snapshot.jpg",
                "aps2": aps2,
                "func": func,
                "info": info,
            }
        ).encode("utf-8")

        event = parse_push_event(payload)

        self.assertEqual(event["event_guid"], "guid-1")
        self.assertEqual(event["img"], "https://example.test/snapshot.jpg")
        self.assertEqual(event["message"], "Doorbell")
        self.assertEqual(event["name"], "MainDoor")
        self.assertEqual(event["time_stamp"], 1234567890)
        self.assertEqual(event["orientation"], 90)

        message = parse_push_frame(XHomePushFrame(PUSH_CMD_EVENT, payload))
        self.assertEqual(message.kind, "event")
        self.assertEqual(message.event, event)


if __name__ == "__main__":
    unittest.main()
