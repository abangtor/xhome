from __future__ import annotations

from pathlib import Path
import unittest


CONFIG_FLOW_PATH = Path(__file__).resolve().parents[1] / "custom_components" / "xhome" / "config_flow.py"


class ConfigFlowCompatibilityTests(unittest.TestCase):
    def test_options_flow_does_not_assign_read_only_config_entry_property(self):
        source = CONFIG_FLOW_PATH.read_text()

        self.assertNotIn("self.config_entry =", source)
        self.assertIn("self._config_entry = config_entry", source)

    def test_local_push_option_is_available(self):
        source = CONFIG_FLOW_PATH.read_text()

        self.assertIn("CONF_LOCAL_PUSH_ENABLED", source)
        self.assertIn("DEFAULT_LOCAL_PUSH_ENABLED", source)


if __name__ == "__main__":
    unittest.main()
