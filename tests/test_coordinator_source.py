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

    def test_local_push_worker_tracks_runtime_status(self):
        source = COORDINATOR_PATH.read_text()

        self.assertIn("def local_push_status", source)
        self.assertIn('"registered_token_tail"', source)
        self.assertIn('"last_frame_command"', source)
        self.assertIn('"events"', source)

    def test_local_push_worker_reregisters_token_after_reconnect(self):
        source = _function_source("_local_push_worker")

        self.assertIn("self._local_push_registered_token = None", source)
        self.assertIn('"registered": False', source)

    def test_device_refresh_retries_api_400_or_401_by_refreshing_token(self):
        source = _function_source("_update_data")
        full_source = COORDINATOR_PATH.read_text()

        self.assertIn("_is_retriable_device_list_error(err)", source)
        self.assertIn("self.client.token = None", source)
        self.assertEqual(source.count("self.client.list_devices_resilient()"), 2)
        self.assertIn("def _is_retriable_device_list_error", full_source)
        self.assertIn("err.status_code in {400, 401}", full_source)

    def test_lock_state_is_derived_from_lock_and_unlock_events(self):
        source = COORDINATOR_PATH.read_text()

        self.assertIn("_latest_lock_state_events", source)
        self.assertIn("def latest_lock_state_event", source)
        self.assertIn("def lock_state", source)
        self.assertIn("def _update_latest_lock_state_events", source)
        self.assertIn('if event_kind == "lock":', source)
        self.assertIn("return True", source)
        self.assertIn('if event_kind == "unlock":', source)
        self.assertIn("return False", source)
        self.assertIn("_cache_manual_unlock(uid)", source)
        self.assertIn('"lock_user_name": "Home Assistant"', source)
        self.assertIn("self._latest_unlock_events[uid] = latest", source)


if __name__ == "__main__":
    unittest.main()
