from __future__ import annotations

import unittest

from xhome.password import (
    RAND_KEY_ALPHABET,
    XHomePasswordError,
    decode_temporary_password,
    encode_temporary_password,
    generate_rand_key,
)


try:
    import cryptography  # noqa: F401

    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False


class TemporaryPasswordTests(unittest.TestCase):
    def test_generate_rand_key_matches_app_shape(self):
        rand_key = generate_rand_key()

        self.assertEqual(len(rand_key), 16)
        self.assertTrue(set(rand_key) <= set(RAND_KEY_ALPHABET))

    def test_rejects_short_uuid(self):
        with self.assertRaises(XHomePasswordError):
            encode_temporary_password("123456", "short", "0123456789abcdef")

    @unittest.skipUnless(HAS_CRYPTOGRAPHY, "cryptography is not installed in this test environment")
    def test_encode_temporary_password_vector(self):
        data, rand_key = encode_temporary_password("246810", "abcd1234567890abcdef", "0123456789abcdef")

        self.assertEqual(data, "i0Hhoh8s9dDYLYbonZ5fcQ==")
        self.assertEqual(rand_key, "0123456789abcdef")
        self.assertEqual(decode_temporary_password(data, "abcd1234567890abcdef", rand_key), "246810")


if __name__ == "__main__":
    unittest.main()
