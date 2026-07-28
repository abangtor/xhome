"""Camera platform for the XHome Home Assistant integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_LIVE_STREAM_URL_TEMPLATE, DOMAIN
from .coordinator import XHomeDataUpdateCoordinator
from .entity import XHomeEntity
from .helpers import redact_uid


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up XHome camera entities."""

    coordinator: XHomeDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(XHomeLiveCamera(coordinator, uid) for uid in coordinator.data.devices)


class XHomeLiveCamera(XHomeEntity, Camera):
    """Live-stream camera backed by an external XHome P2P bridge."""

    _attr_translation_key = "live_camera"
    _attr_should_poll = False

    def __init__(self, coordinator: XHomeDataUpdateCoordinator, uid: str) -> None:
        """Initialize the live camera entity."""

        Camera.__init__(self)
        XHomeEntity.__init__(self, coordinator, uid, "live_camera")

    @property
    def available(self) -> bool:
        """Return whether the live camera has a configured stream source."""

        return super().available and bool(live_stream_url_template(self.coordinator.config_entry.options))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return non-sensitive live-stream metadata."""

        attrs = super().extra_state_attributes
        data = self.device_data
        attrs.update(
            {
                "live_stream_configured": bool(live_stream_url_template(self.coordinator.config_entry.options)),
                "bridge": "external",
                "native_transport": "IVIEWSAVAPIs",
                "native_media_header_bytes": 40,
                "video_codec": "h264",
                "audio_codec": "g711",
            }
        )
        if data is not None and data.device_id is not None:
            attrs["device_id"] = data.device_id
        return {key: value for key, value in attrs.items() if value is not None}

    async def stream_source(self) -> str | None:
        """Return the configured external stream URL for Home Assistant."""

        template = live_stream_url_template(self.coordinator.config_entry.options)
        if not template or self.device_data is None:
            return None
        return render_live_stream_url(
            template,
            uid=self.uid,
            uid_tail=redact_uid(self.uid),
            device_id=self.device_data.device_id,
            model=self.device_data.model,
        )


def live_stream_url_template(options: dict[str, Any]) -> str:
    """Return the configured live-stream sidecar URL template."""

    return str(options.get(CONF_LIVE_STREAM_URL_TEMPLATE) or "").strip()


def render_live_stream_url(
    template: str,
    *,
    uid: str,
    uid_tail: str,
    device_id: int | None,
    model: str | None,
) -> str:
    """Render a sidecar stream URL with safe known placeholders."""

    values = {
        "uid": uid,
        "uid_tail": uid_tail,
        "device_id": "" if device_id is None else str(device_id),
        "model": "" if model is None else str(model),
    }
    return template.format_map(_SafeFormatMap(values))


class _SafeFormatMap(dict[str, str]):
    """Leave unknown placeholders intact for sidecar-specific routing."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"
