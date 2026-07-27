from __future__ import annotations

import struct
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BRAND_ICON = ROOT / "custom_components" / "xhome" / "brand" / "icon.png"
COMPONENT_ICON = ROOT / "custom_components" / "xhome" / "icon.png"


class AssetTests(unittest.TestCase):
    def test_hacs_brand_icon_is_valid_png(self):
        data = BRAND_ICON.read_bytes()

        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(_png_dimensions(data), (304, 304))

    def test_component_icon_matches_brand_icon(self):
        self.assertEqual(COMPONENT_ICON.read_bytes(), BRAND_ICON.read_bytes())


def _png_dimensions(data: bytes) -> tuple[int, int]:
    """Return PNG IHDR width and height."""

    return struct.unpack(">II", data[16:24])


if __name__ == "__main__":
    unittest.main()
