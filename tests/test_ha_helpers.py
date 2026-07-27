from __future__ import annotations

import importlib.util
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

    def test_event_payload_redacts_uid_and_omits_media_url(self):
        payload = helpers.event_payload(
            {"uid": "abcdef123456", "name": "MainDoor", "id": 5},
            {"id": 1, "event_guid": "guid-1", "type": "1", "time_stamp": 123, "m_oss_url": "https://secret"},
        )

        self.assertEqual(payload["device_name"], "MainDoor")
        self.assertEqual(payload["uid_tail"], "...123456")
        self.assertTrue(payload["has_image"])
        self.assertNotIn("m_oss_url", payload)

    def test_event_has_image_accepts_image_fields(self):
        self.assertTrue(helpers.event_has_image({"img": "snapshot.jpg"}))
        self.assertTrue(helpers.event_has_image({"m_oss_url": "https://example.test/snapshot.jpg"}))
        self.assertFalse(helpers.event_has_image({"event_guid": "guid-1"}))

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

    def test_media_url_from_event_requires_http_url(self):
        self.assertEqual(
            helpers.media_url_from_event({"m_oss_url": "https://example.test/snapshot.jpg"}),
            "https://example.test/snapshot.jpg",
        )
        self.assertIsNone(helpers.media_url_from_event({"img": "relative/snapshot.jpg"}))


if __name__ == "__main__":
    unittest.main()
