from __future__ import annotations

import importlib.util
import base64
import json
from pathlib import Path
import sys
import types
import unittest

HELPERS_PATH = Path(__file__).resolve().parents[1] / "custom_components" / "xhome" / "helpers.py"

COMPONENT_PATH = HELPERS_PATH.parent
CUSTOM_COMPONENTS_PATH = COMPONENT_PATH.parent

sys.modules.setdefault("custom_components", types.ModuleType("custom_components"))
sys.modules["custom_components"].__path__ = [str(CUSTOM_COMPONENTS_PATH)]
package = types.ModuleType("custom_components.xhome")
package.__path__ = [str(COMPONENT_PATH)]
sys.modules.setdefault("custom_components.xhome", package)

spec = importlib.util.spec_from_file_location("custom_components.xhome.helpers", HELPERS_PATH)
helpers = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(helpers)


class HomeAssistantHelperTests(unittest.TestCase):
    def test_device_helpers(self):
        device = {"uid": "abcdef123456", "name": "MainDoor"}

        self.assertEqual(helpers.device_uid(device), "abcdef123456")
        self.assertEqual(helpers.device_name(device), "MainDoor")
        self.assertEqual(helpers.redact_uid("abcdef123456"), "...123456")
        self.assertEqual(len(helpers.device_key("abcdef123456")), 16)

    def test_value_helpers(self):
        self.assertEqual(helpers.int_value("42.0"), 42)
        self.assertIsNone(helpers.int_value("nope"))
        self.assertTrue(helpers.bool_value("online"))
        self.assertFalse(helpers.bool_value("0"))
        self.assertIsNone(helpers.bool_value("maybe"))

    def test_notify_category_mask_helpers(self):
        self.assertTrue(helpers.notify_category_enabled(0, (0, 1)))
        self.assertTrue(helpers.notify_category_enabled(1, (0, 1)))
        self.assertFalse(helpers.notify_category_enabled(3, (0,)))

        mask = helpers.set_notify_category_enabled(0, (5, 6), False)
        self.assertEqual(mask, 193)
        self.assertFalse(helpers.notify_category_enabled(mask, (5, 6)))

        mask = helpers.set_notify_category_enabled(mask, (5, 6), True)
        self.assertEqual(mask, 1)
        self.assertTrue(helpers.notify_category_enabled(mask, (5, 6)))

    def test_unwrap_dict(self):
        self.assertEqual(helpers.unwrap_dict({"resultData": {"battery": 100}}), {"battery": 100})
        self.assertEqual(helpers.unwrap_dict({"data": {"battery": 100}}), {"battery": 100})
        self.assertEqual(helpers.unwrap_dict({"data": []}), {})

    def test_event_records_extract_event_lists(self):
        payload = {
            "resultData": {
                "eventList": [
                    {"id": 1, "uid": "abc", "event_guid": "guid-1", "type": "1", "time_stamp": 123},
                ],
                "oneList": [
                    {"id": 2, "uid": "abc", "event_guid": "guid-2", "type": "0", "time_stamp": 124},
                ],
            }
        }

        self.assertEqual([event["event_guid"] for event in helpers.event_records(payload)], ["guid-1", "guid-2"])

    def test_event_key_prefers_event_guid(self):
        self.assertEqual(
            helpers.event_key("abc", {"id": 1, "event_guid": "guid-1", "type": "1"}),
            "abc:guid:guid-1",
        )

    def test_doorbell_event_detection(self):
        self.assertTrue(helpers.is_doorbell_event({"type": "1"}))
        self.assertTrue(helpers.is_doorbell_event({"action": "call"}))
        self.assertFalse(helpers.is_doorbell_event({"type": "2", "info": "fingerprint unlock"}))

    def test_event_kind_from_push_type(self):
        self.assertEqual(helpers.event_kind({"type": "0"}), "motion")
        self.assertEqual(helpers.event_kind({"type": "2"}), "unlock")
        self.assertEqual(helpers.event_kind({"type": "3"}), "unlock")
        self.assertEqual(helpers.event_kind({"type": "4"}), "low_battery")
        self.assertEqual(helpers.event_kind({"type": "20"}), "offline")
        self.assertEqual(helpers.event_kind({"type": "21"}), "online")

    def test_event_kind_from_encoded_lock_event(self):
        encoded = base64.b64encode(
            json.dumps(
                {
                    "user_id": "1",
                    "event_type": "15",
                    "event_device": "LOCK_PUSH",
                    "content": "09",
                    "app_user": "Torsten",
                }
            ).encode("utf-8")
        ).decode("ascii")

        event = {"type": "6", "info": encoded}

        self.assertEqual(helpers.event_kind(event), "unlock")
        self.assertEqual(helpers.lock_event_details(event)["lock_event_type"], "15")
        self.assertEqual(helpers.lock_event_details(event)["lock_event_type_name"], "unlock")
        self.assertEqual(helpers.lock_event_details(event)["lock_event_content"], "09")
        self.assertEqual(helpers.lock_event_details(event)["lock_event_content_name"], "app_unlock")
        self.assertEqual(helpers.event_bus_types(event), ("xhome_unlock",))

        payload = helpers.event_payload({"uid": "abcdef123456", "name": "MainDoor"}, event)
        self.assertEqual(payload["event_kind"], "unlock")
        self.assertEqual(payload["lock_event_type_name"], "unlock")
        self.assertEqual(payload["lock_event_content_name"], "app_unlock")

    def test_event_payload_adds_configured_lock_user_mapping(self):
        encoded = base64.b64encode(
            json.dumps(
                {
                    "user_id": "32",
                    "event_type": "15",
                    "event_device": "LOCK_PUSH",
                    "content": "00",
                }
            ).encode("utf-8")
        ).decode("ascii")
        options = {
            "lock_user_mappings": {
                "abcdef123456": [
                    {"name": "torsten", "person": "person.torsten", "ids": ["13", "14", "32"]},
                    {"name": "joanne", "person": "person.joanne", "ids": ["24", "25", "66"]},
                ],
            }
        }

        payload = helpers.event_payload(
            {"uid": "abcdef123456", "name": "MainDoor"},
            {"type": "6", "info": encoded},
            options,
        )

        self.assertEqual(payload["lock_event_user_id"], "32")
        self.assertEqual(payload["lock_user_name"], "torsten")
        self.assertEqual(payload["lock_person"], "person.torsten")

    def test_event_payload_leaves_unmapped_lock_user_without_name(self):
        encoded = base64.b64encode(
            json.dumps(
                {
                    "user_id": "99",
                    "event_type": "15",
                    "event_device": "LOCK_PUSH",
                    "content": "00",
                }
            ).encode("utf-8")
        ).decode("ascii")
        options = {"lock_user_mappings": {"abcdef123456": [{"name": "torsten", "ids": ["32"]}]}}

        payload = helpers.event_payload(
            {"uid": "abcdef123456", "name": "MainDoor"},
            {"type": "6", "info": encoded},
            options,
        )

        self.assertEqual(payload["lock_event_user_id"], "99")
        self.assertNotIn("lock_user_name", payload)
        self.assertNotIn("lock_person", payload)

    def test_encoded_remote_lock_is_lock_event(self):
        encoded = base64.b64encode(
            json.dumps(
                {
                    "user_id": "1",
                    "event_type": "21",
                    "event_device": "LOCK_PUSH",
                    "content": "",
                    "app_user": "Torsten",
                }
            ).encode("utf-8")
        ).decode("ascii")

        event = {"type": "6", "info": encoded}
        details = helpers.lock_event_details(event)

        self.assertEqual(helpers.event_kind(event), "lock")
        self.assertEqual(helpers.event_bus_types(event), ("xhome_lock",))
        self.assertEqual(details["lock_event_type"], "21")
        self.assertEqual(details["lock_event_type_name"], "remote_lock")
        self.assertIsNone(details["lock_event_content_name"])

    def test_encoded_lock_content_uses_lock_specific_mapping(self):
        for lock_event_type in ("13", "14"):
            with self.subTest(lock_event_type=lock_event_type):
                outside = base64.b64encode(
                    json.dumps(
                        {
                            "user_id": "1",
                            "event_type": lock_event_type,
                            "event_device": "LOCK_PUSH",
                            "content": "13",
                            "app_user": "Torsten",
                        }
                    ).encode("utf-8")
                ).decode("ascii")
                inside = base64.b64encode(
                    json.dumps(
                        {
                            "user_id": "1",
                            "event_type": lock_event_type,
                            "event_device": "LOCK_PUSH",
                            "content": "0A",
                            "app_user": "Torsten",
                        }
                    ).encode("utf-8")
                ).decode("ascii")

                outside_event = {"type": "6", "info": outside}
                inside_event = {"type": "6", "info": inside}

                self.assertEqual(helpers.event_kind(outside_event), "lock")
                self.assertEqual(helpers.event_bus_types(outside_event), ("xhome_lock",))
                self.assertEqual(
                    helpers.lock_event_details(outside_event)["lock_event_content_name"],
                    "outside_button_lock",
                )
                self.assertEqual(
                    helpers.lock_event_details(inside_event)["lock_event_content_name"],
                    "inside_button_lock",
                )

    def test_event_bus_types_include_specific_and_alarm_events(self):
        self.assertEqual(helpers.event_bus_types({"type": "2"}), ("xhome_unlock",))
        self.assertEqual(helpers.event_bus_types({"type": "1"}), ("xhome_doorbell",))
        self.assertEqual(
            helpers.event_bus_types({"type": "25", "info": "tamper alarm"}),
            ("xhome_tamper", "xhome_alarm"),
        )

    def test_event_payload_redacts_uid_and_omits_media_url(self):
        payload = helpers.event_payload(
            {"uid": "abcdef123456", "name": "MainDoor", "id": 5},
            {"id": 1, "event_guid": "guid-1", "type": "1", "time_stamp": 123, "m_oss_url": "https://secret"},
        )

        self.assertEqual(payload["device_name"], "MainDoor")
        self.assertEqual(payload["uid_tail"], "...123456")
        self.assertEqual(payload["event_kind"], "doorbell")
        self.assertEqual(payload["event_type_name"], "call")
        self.assertTrue(payload["has_image"])
        self.assertTrue(payload["has_media"])
        self.assertNotIn("m_oss_url", payload)

    def test_event_has_image_accepts_image_fields(self):
        self.assertTrue(helpers.event_has_image({"img": "snapshot.jpg"}))
        self.assertTrue(helpers.event_has_image({"m_oss_url": "https://example.test/snapshot.jpg"}))
        self.assertFalse(helpers.event_has_image({"event_guid": "guid-1"}))

    def test_event_has_media_accepts_event_guid(self):
        self.assertTrue(helpers.event_has_media({"event_guid": "guid-1"}))
        self.assertTrue(helpers.event_has_media({"eventGuid": "guid-2"}))
        self.assertFalse(helpers.event_has_media({"id": 1, "type": "1"}))

    def test_media_items_extract_oss_urls(self):
        payload = {
            "resultData": {
                "data": [
                    {
                        "oss_url": "https://example.test/snapshot.jpg?token=secret",
                        "file_name": "snapshot.jpg",
                        "exp_time": 456,
                    }
                ]
            }
        }

        item = helpers.first_media_item(payload)

        self.assertIsNotNone(item)
        self.assertEqual(helpers.media_url_from_item(item), "https://example.test/snapshot.jpg?token=secret")
        self.assertEqual(helpers.guess_media_content_type(item["oss_url"], item["file_name"]), "image/jpeg")

    def test_first_video_media_item_prefers_video_urls(self):
        payload = {
            "resultData": {
                "data": [
                    {"oss_url": "https://example.test/snapshot.jpg", "file_name": "snapshot.jpg"},
                    {"oss_url": "https://example.test/event.mp4?token=secret", "file_name": "event.mp4"},
                ]
            }
        }

        item = helpers.first_video_media_item(payload)

        self.assertIsNotNone(item)
        self.assertEqual(helpers.media_url_from_item(item), "https://example.test/event.mp4?token=secret")
        self.assertTrue(helpers.is_video_media(item["oss_url"], file_name=item["file_name"]))

    def test_media_url_from_event_requires_http_url(self):
        encoded_url = base64.b64encode(b"https://example.test/snapshot.jpg?token=secret").decode("ascii")

        self.assertEqual(
            helpers.media_url_from_event({"m_oss_url": "https://example.test/snapshot.jpg"}),
            "https://example.test/snapshot.jpg",
        )
        self.assertEqual(
            helpers.media_url_from_event({"img": encoded_url}),
            "https://example.test/snapshot.jpg?token=secret",
        )
        self.assertEqual(
            helpers.media_url_from_item({"oss_url": encoded_url}),
            "https://example.test/snapshot.jpg?token=secret",
        )
        self.assertIsNone(helpers.media_url_from_event({"img": "relative/snapshot.jpg"}))


if __name__ == "__main__":
    unittest.main()
