from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE_API = ROOT / "src" / "xhome"
VENDORED_API = ROOT / "custom_components" / "xhome" / "api"


class VendorSyncTests(unittest.TestCase):
    def test_runtime_api_copy_matches_source_package(self):
        for filename in ["client.py", "constants.py", "exceptions.py", "models.py", "signing.py"]:
            with self.subTest(filename=filename):
                self.assertEqual((VENDORED_API / filename).read_text(), (SOURCE_API / filename).read_text())


if __name__ == "__main__":
    unittest.main()
