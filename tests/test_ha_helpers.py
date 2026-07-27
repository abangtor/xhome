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


if __name__ == "__main__":
    unittest.main()
