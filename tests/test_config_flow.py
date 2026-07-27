from __future__ import annotations

from pathlib import Path
import unittest


CONFIG_FLOW_PATH = Path(__file__).resolve().parents[1] / "custom_components" / "xhome" / "config_flow.py"


class ConfigFlowCompatibilityTests(unittest.TestCase):
    def test_options_flow_does_not_assign_read_only_config_entry_property(self):
        source = CONFIG_FLOW_PATH.read_text()

        self.assertNotIn("self.config_entry =", source)
        self.assertIn("self._config_entry = config_entry", source)


if __name__ == "__main__":
    unittest.main()
