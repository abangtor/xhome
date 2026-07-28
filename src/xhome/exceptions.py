"""Exceptions raised by the XHome client."""

from __future__ import annotations


class XHomeError(Exception):
    """Base class for XHome client errors."""


class XHomeAuthError(XHomeError):
    """Authentication is missing, expired, or rejected by the API."""


class XHomeAPIError(XHomeError):
    """The XHome API returned an error response."""

    def __init__(self, message: str, *, status_code: int | None = None, payload: object | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class XHomePushError(XHomeError):
    """The XHome native push socket failed."""
