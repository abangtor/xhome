from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
INIT_PATH = ROOT / "custom_components" / "xhome" / "__init__.py"
COORDINATOR_PATH = ROOT / "custom_components" / "xhome" / "coordinator.py"
SERVICES_PATH = ROOT / "custom_components" / "xhome" / "services.yaml"


class HomeAssistantServiceTests(unittest.TestCase):
    def test_api_services_are_registered_and_documented(self):
        init_source = INIT_PATH.read_text()
        services_yaml = SERVICES_PATH.read_text()
        service_names = [
            "list_devices",
            "local_push_status",
            "get_screen_light_config",
            "get_app_lock_status",
            "set_unlock_type",
            "list_lock_members",
            "upsert_lock_member",
            "update_event_member",
            "list_temporary_passwords",
            "add_temporary_password",
            "add_temporary_password_raw",
            "rename_temporary_password",
            "delete_temporary_password",
        ]

        for service in service_names:
            with self.subTest(service=service):
                self.assertIn(f'"{service}"', init_source)
                self.assertIn(f"{service}:", services_yaml)

    def test_entity_backed_setting_services_are_not_registered(self):
        init_source = INIT_PATH.read_text()
        services_yaml = SERVICES_PATH.read_text()
        service_names = [
            "set_screen_light_timeout",
            "set_battery_display",
            "set_weather_forecast",
            "set_call_screen",
            "set_standby_mode",
            "set_target_ev",
            "set_remote_unlock_limit",
        ]

        for service in service_names:
            with self.subTest(service=service):
                self.assertNotIn(f'"{service}"', init_source)
                self.assertNotIn(f"{service}:", services_yaml)

    def test_access_changing_temporary_password_services_require_confirmation(self):
        init_source = INIT_PATH.read_text()
        services_yaml = SERVICES_PATH.read_text()

        self.assertIn("_require_confirmed(call)", init_source)
        self.assertIn("add_temporary_password_raw:", services_yaml)
        self.assertIn("delete_temporary_password:", services_yaml)
        self.assertGreaterEqual(services_yaml.count("confirm:"), 2)

    def test_service_helper_does_not_collide_with_api_name_kwarg(self):
        init_source = INIT_PATH.read_text()
        coordinator_source = COORDINATOR_PATH.read_text()

        self.assertIn("call_name: str", init_source)
        self.assertIn("call_name: str", coordinator_source)
        self.assertIn("name=call.data[CONF_NAME]", init_source)
        self.assertNotIn("\n    name: str,\n    method_name", init_source)
        self.assertNotIn("\n        name: str,\n        method_name", coordinator_source)

    def test_external_live_stream_sidecar_service_is_not_registered(self):
        init_source = INIT_PATH.read_text()
        services_yaml = SERVICES_PATH.read_text()

        self.assertNotIn('"prepare_live_stream"', init_source)
        self.assertNotIn("prepare_live_stream:", services_yaml)


if __name__ == "__main__":
    unittest.main()
