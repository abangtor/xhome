"""Small value objects for the XHome client."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Region:
    key: str
    server_id: int
    rest_url: str
    push_host: str
    native_iot_host: str


@dataclass
class LoginSession:
    user_id: int | None
    token: str
    refresh_key: str | None = None
    logout_status: int | None = None
    server_time: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)
