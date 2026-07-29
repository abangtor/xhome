"""Camera platform for the XHome Home Assistant integration."""

from __future__ import annotations

import asyncio
import logging
from threading import Event, Thread
from typing import Any

from aiohttp import web
from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api.live import LiveAppMediaFrame, LiveSessionMetadata, MediaType
from .api.live_p2p import XHomeP2PRendezvousProbe
from .api.live_transport import XHomeLiveCloudTransport, extract_p2p_servers
from .const import CONF_LIVE_STREAM_URL_TEMPLATE, DOMAIN
from .coordinator import XHomeDataUpdateCoordinator, XHomeLiveStreamSession
from .entity import XHomeEntity
from .helpers import redact_uid

LOGGER = logging.getLogger(__name__)
MJPEG_BOUNDARY = b"xhome"
MJPEG_STREAM_DURATION = 3600.0
MJPEG_FIRST_FRAME_TIMEOUT = 30.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up XHome camera entities."""

    coordinator: XHomeDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(XHomeLiveCamera(coordinator, uid) for uid in coordinator.data.devices)


class XHomeLiveCamera(XHomeEntity, Camera):
    """Live-stream camera backed by the native XHome P2P transport."""

    _attr_translation_key = "live_camera"
    _attr_should_poll = False

    def __init__(self, coordinator: XHomeDataUpdateCoordinator, uid: str) -> None:
        """Initialize the live camera entity."""

        Camera.__init__(self)
        XHomeEntity.__init__(self, coordinator, uid, "live_camera")

    @property
    def available(self) -> bool:
        """Return whether the live camera can prepare a native stream."""

        return super().available and self.device_data is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return non-sensitive live-stream metadata."""

        attrs = super().extra_state_attributes
        data = self.device_data
        attrs.update(
            {
                "live_stream_configured": bool(live_stream_url_template(self.coordinator.config_entry.options)),
                "embedded_live_stream": True,
                "bridge": "embedded",
                "native_transport": "portable_p2p",
                "native_media_header_bytes": 40,
                "video_codec": "mjpeg",
                "audio_codec": "g711",
            }
        )
        if data is not None and data.device_id is not None:
            attrs["device_id"] = data.device_id
        return {key: value for key, value in attrs.items() if value is not None}

    async def stream_source(self) -> str | None:
        """Return the optional configured external stream URL for Home Assistant."""

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

    async def handle_async_mjpeg_stream(self, request: web.Request) -> web.StreamResponse:
        """Serve a native XHome live session as a Home Assistant MJPEG stream."""

        if self.device_data is None:
            raise HomeAssistantError("XHome device is unavailable")

        session = await self.coordinator.async_prepare_live_stream(self.uid)
        frame_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1)
        stop_event = Event()
        loop = asyncio.get_running_loop()

        def on_frame(frame: LiveAppMediaFrame) -> None:
            if frame.media_type != MediaType.JPEG_FRAME:
                return
            loop.call_soon_threadsafe(_replace_latest_frame, frame_queue, frame.payload)

        thread = Thread(
            target=_run_native_mjpeg_worker,
            args=(session, on_frame, stop_event),
            name=f"xhome-live-{redact_uid(self.uid)}",
            daemon=True,
        )
        thread.start()

        response = web.StreamResponse(
            status=200,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Content-Type": f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY.decode('ascii')}",
            },
        )
        await response.prepare(request)

        try:
            while not stop_event.is_set():
                try:
                    frame = await asyncio.wait_for(frame_queue.get(), timeout=MJPEG_FIRST_FRAME_TIMEOUT)
                except TimeoutError:
                    if not thread.is_alive():
                        break
                    continue
                await response.write(
                    b"--"
                    + MJPEG_BOUNDARY
                    + b"\r\nContent-Type: image/jpeg\r\n"
                    + f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii")
                    + frame
                    + b"\r\n"
                )
                await response.drain()
        except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
            raise
        finally:
            stop_event.set()
            await self.hass.async_add_executor_job(thread.join, 2)

        return response


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


def _replace_latest_frame(frame_queue: asyncio.Queue[bytes], frame: bytes) -> None:
    """Keep only the newest frame when the browser reads slowly."""

    while not frame_queue.empty():
        try:
            frame_queue.get_nowait()
        except asyncio.QueueEmpty:
            break
    try:
        frame_queue.put_nowait(frame)
    except asyncio.QueueFull:
        pass


def _run_native_mjpeg_worker(
    session: XHomeLiveStreamSession,
    on_frame: Any,
    stop_event: Event,
) -> None:
    """Run one blocking native live session for the MJPEG response."""

    metadata = LiveSessionMetadata(
        uid=session.uid,
        token=session.token,
        native_iot_host=session.native_iot_host,
        device_id=session.device_id,
        model=session.model,
    )

    try:
        with XHomeLiveCloudTransport(metadata, verify_tls=False) as transport:
            try:
                transport.login()
                native_frames = transport.read_available(duration=1.0)
                transport.send_frame(metadata.start_command)
                native_frames.extend(transport.read_available(duration=3.0))
                if not extract_p2p_servers(native_frames):
                    native_frames.extend(transport.read_available(duration=5.0))

                relays = _unique_p2p_relays(extract_p2p_servers(native_frames))
                if not relays:
                    raise RuntimeError("Native IoT session did not return any P2P relays")

                XHomeP2PRendezvousProbe(
                    uid=metadata.uid,
                    relays=relays,
                    direct_punch_enabled=True,
                ).run(
                    duration=MJPEG_STREAM_DURATION,
                    on_frame=on_frame,
                    stop_event=stop_event,
                )
            finally:
                transport.send_frame(metadata.stop_command)
    except Exception as err:  # noqa: BLE001
        LOGGER.debug("XHome native live stream stopped: %s", err)
    finally:
        stop_event.set()


def _unique_p2p_relays(servers: list[dict[str, Any]]) -> list[tuple[str, int]]:
    """Return unique relay host/port pairs from native IoT command-9 frames."""

    relays: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for server in servers:
        if "IP" not in server or "Port" not in server:
            continue
        relay = (str(server["IP"]), int(server["Port"]))
        if relay not in seen:
            seen.add(relay)
            relays.append(relay)
    return relays
