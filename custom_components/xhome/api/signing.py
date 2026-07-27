"""Signing helpers copied from the Android app's request builders."""

from __future__ import annotations

import hashlib

from .constants import API_KEY, LONG_SALT


def sha1_hex(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def login_sign(username: str, password: str, timestamp: int) -> str:
    value = f"{username}{API_KEY}{password}{LONG_SALT}{timestamp}".lower()
    return sha1_hex(value)


def token_time_sign(token: str, subject: str | int, timestamp: int) -> str:
    return sha1_hex(f"{token}{API_KEY}{subject}{LONG_SALT}{timestamp}")


def token_subject_sign(token: str, subject: str | int) -> str:
    return sha1_hex(f"{token}{API_KEY}{subject}{LONG_SALT}")
