from __future__ import annotations

import collections
import importlib.util
from pathlib import Path
import sys
import types
import unittest

IMAGE_PATH = Path(__file__).resolve().parents[1] / "custom_components" / "xhome" / "image.py"
COMPONENT_PATH = IMAGE_PATH.parent
CUSTOM_COMPONENTS_PATH = COMPONENT_PATH.parent


def _install_module(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    sys.modules[name] = module
    return module


sys.modules.setdefault("custom_components", types.ModuleType("custom_components"))
sys.modules["custom_components"].__path__ = [str(CUSTOM_COMPONENTS_PATH)]

package = types.ModuleType("custom_components.xhome")
package.__path__ = [str(COMPONENT_PATH)]
sys.modules["custom_components.xhome"] = package

ha_image = _install_module("homeassistant.components.image")


class FakeImageEntity:
    def __init__(self, hass, verify_ssl: bool = False) -> None:
        self.hass = hass
        self.verify_ssl = verify_ssl
        self.access_tokens = collections.deque(["token"], maxlen=2)


ha_image.ImageEntity = FakeImageEntity

ha_config_entries = _install_module("homeassistant.config_entries")
ha_config_entries.ConfigEntry = object

ha_core = _install_module("homeassistant.core")
ha_core.HomeAssistant = object

ha_entity_platform = _install_module("homeassistant.helpers.entity_platform")
ha_entity_platform.AddEntitiesCallback = object

xhome_const = _install_module("custom_components.xhome.const")
xhome_const.CONF_IMAGE_ROTATION = "image_rotation"
xhome_const.DEFAULT_IMAGE_ROTATION = 0
xhome_const.DOMAIN = "xhome"
xhome_const.IMAGE_ROTATIONS = [0, 90, 180, 270]

xhome_coordinator = _install_module("custom_components.xhome.coordinator")
xhome_coordinator.XHomeDataUpdateCoordinator = object
xhome_coordinator.XHomeLatestEventMedia = object

xhome_entity = _install_module("custom_components.xhome.entity")


class FakeXHomeEntity:
    def __init__(self, coordinator, uid: str, suffix: str) -> None:
        self.coordinator = coordinator
        self.uid = uid
        self._attr_unique_id = f"{uid}_{suffix}"

    @property
    def available(self) -> bool:
        return True

    @property
    def extra_state_attributes(self) -> dict:
        return {}


xhome_entity.XHomeEntity = FakeXHomeEntity

spec = importlib.util.spec_from_file_location("custom_components.xhome.image", IMAGE_PATH)
image = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(image)


class ImageEntityTests(unittest.TestCase):
    def test_latest_event_image_initializes_image_access_token(self):
        coordinator = types.SimpleNamespace(
            hass=object(),
            config_entry=types.SimpleNamespace(options={}),
            latest_event_media=lambda uid: None,
            downloaded_event_media=lambda uid: None,
        )

        entity = image.XHomeLatestEventImage(coordinator, "abc")

        self.assertEqual(entity.uid, "abc")
        self.assertEqual(entity.access_tokens[-1], "token")

    def test_image_rotation_degrees_normalizes_options(self):
        self.assertEqual(image.image_rotation_degrees({"image_rotation": "90"}), 90)
        self.assertEqual(image.image_rotation_degrees({"image_rotation": "bad"}), 0)
        self.assertEqual(image.image_rotation_degrees({"image_rotation": "45"}), 0)

    def test_rotate_image_bytes_noops_without_rotation(self):
        self.assertEqual(image.rotate_image_bytes(b"not really an image", 0, "image/jpeg"), b"not really an image")


if __name__ == "__main__":
    unittest.main()
