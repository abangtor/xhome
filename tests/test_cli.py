from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from xhome import cli


class CliTests(unittest.TestCase):
    def test_auth_add_requires_confirmation(self):
        args = cli.build_parser().parse_args(["auth-add", "abc", "--name", "Guest", "--password", "246810"])

        with self.assertRaises(SystemExit):
            cli.cmd_auth_add(args)

    def test_auth_add_calls_plain_password_method(self):
        args = cli.build_parser().parse_args(
            [
                "auth-add",
                "abcd1234567890abcdef",
                "--name",
                "Guest",
                "--password",
                "246810",
                "--begin-time",
                "10",
                "--end-time",
                "20",
                "--week",
                "127",
                "--yes",
            ]
        )
        client = Mock()

        with patch("xhome.cli.logged_in_client", return_value=client):
            cli.cmd_auth_add(args)

        client.add_temporary_password.assert_called_once_with(
            "abcd1234567890abcdef",
            name="Guest",
            password="246810",
            begin_time=10,
            end_time=20,
            start_time=0,
            stop_time=0,
            total_times=0,
            week=127,
            user_type=2,
            auth_type=1,
            entry="app",
        )


if __name__ == "__main__":
    unittest.main()
