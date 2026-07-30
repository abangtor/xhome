from __future__ import annotations

from pathlib import Path
import unittest


CONFIG_FLOW_PATH = Path(__file__).resolve().parents[1] / "custom_components" / "xhome" / "config_flow.py"
STRINGS_PATH = Path(__file__).resolve().parents[1] / "custom_components" / "xhome" / "strings.json"


class ConfigFlowCompatibilityTests(unittest.TestCase):
    def test_options_flow_does_not_assign_read_only_config_entry_property(self):
        source = CONFIG_FLOW_PATH.read_text()

        self.assertNotIn("self.config_entry =", source)
        self.assertIn("self._config_entry = config_entry", source)

    def test_local_push_option_is_available(self):
        source = CONFIG_FLOW_PATH.read_text()

        self.assertIn("CONF_LOCAL_PUSH_ENABLED", source)
        self.assertIn("DEFAULT_LOCAL_PUSH_ENABLED", source)

    def test_region_option_is_available(self):
        source = CONFIG_FLOW_PATH.read_text()

        self.assertIn("config_entry.options.get(\n                    CONF_REGION", source)
        self.assertIn("selector.SelectSelectorConfig(options=REGIONS)", source)

    def test_image_rotation_option_is_available(self):
        source = CONFIG_FLOW_PATH.read_text()

        self.assertIn("CONF_IMAGE_ROTATION", source)
        self.assertIn("IMAGE_ROTATIONS", source)
        self.assertIn("selector.SelectSelectorMode.DROPDOWN", source)

    def test_external_live_stream_url_template_option_is_not_available(self):
        source = CONFIG_FLOW_PATH.read_text()

        self.assertNotIn("CONF_LIVE_STREAM_URL_TEMPLATE", source)
        self.assertNotIn("DEFAULT_LIVE_STREAM_URL_TEMPLATE", source)

    def test_options_flow_has_lock_user_mapping_steps(self):
        source = CONFIG_FLOW_PATH.read_text()
        strings = STRINGS_PATH.read_text()

        self.assertIn("CONF_LOCK_USER_MAPPINGS", source)
        self.assertIn("async_step_add_lock_user", source)
        self.assertIn("async_step_edit_lock_user", source)
        self.assertIn("async_step_choose_lock_user_mapping", source)
        self.assertIn("async_step_lock_user_mapping", source)
        self.assertIn("recent_unknown_lock_user_ids", source)
        self.assertIn("selector.EntitySelectorConfig(domain=\"person\")", source)
        self.assertIn("async_show_menu", source)
        self.assertIn("\"menu_options\"", strings)
        self.assertIn("\"edit_lock_user\": \"Edit lock user\"", strings)


if __name__ == "__main__":
    unittest.main()
