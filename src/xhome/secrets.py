"""Optional helpers for reading local OpenClaw secret profiles."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_OPENCLAW_SECRETS_FILE = Path("/home/openclaw/.openclaw/secrets.json")


def load_openclaw_auth_profile(
    profile: str = "xhome",
    *,
    secrets_file: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Load one auth profile from OpenClaw's local secrets file.

    Missing files, missing profiles, or malformed profile values return an empty
    dict so callers can keep falling back to environment variables.
    """

    path = Path(secrets_file or os.getenv("OPENCLAW_SECRETS_FILE") or DEFAULT_OPENCLAW_SECRETS_FILE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}

    auth_profiles = data.get("authProfiles")
    if not isinstance(auth_profiles, dict):
        return {}

    value = auth_profiles.get(profile)
    return dict(value) if isinstance(value, dict) else {}
