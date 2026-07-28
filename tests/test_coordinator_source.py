from __future__ import annotations

from pathlib import Path
import unittest


COORDINATOR_PATH = Path(__file__).resolve().parents[1] / "custom_components" / "xhome" / "coordinator.py"


def _function_source(name: str) -> str:
    source = COORDINATOR_PATH.read_text()
    start = source.index(f"    def {name}(")
    next_function = source.find("\n    def ", start + 1)
    if next_function == -1:
        next_function = source.find("\ndef ", start + 1)
    return source[start:next_function if next_function != -1 else len(source)]


class CoordinatorSourceTests(unittest.TestCase):
    def test_local_push_worker_reuses_coordinator_token_instead_of_logging_in_again(self):
        source = _function_source("_new_worker_client")

        self.assertIn("self._ensure_login()", source)
        self.assertIn("token=self.client.require_token()", source)
        self.assertIn("user_id=self.client.require_user_id()", source)
        self.assertNotIn(".login(", source)

    def test_device_refresh_retries_no_user_by_refreshing_token(self):
        source = _function_source("_update_data")

        self.assertIn("_is_no_user_error(err)", source)
        self.assertIn("self.client.token = None", source)
        self.assertEqual(source.count("self.client.list_devices_resilient()"), 2)


if __name__ == "__main__":
    unittest.main()
