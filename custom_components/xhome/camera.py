"""Camera platform for the XHome Home Assistant integration."""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from threading import Event, Thread
from typing import Any

from aiohttp import web
from homeassistant.components.camera import Camera
from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.network import get_url

from .api.live import LiveAppMediaFrame, LiveSessionMetadata, MediaType
from .api.live_p2p import XHomeP2PRendezvousProbe
from .api.live_transport import XHomeLiveCloudTransport, extract_p2p_servers
from .const import CONF_LIVE_STREAM_URL_TEMPLATE, DOMAIN
from .coordinator import XHomeDataUpdateCoordinator, XHomeLiveStreamSession
from .entity import XHomeEntity
from .helpers import redact_uid
from .image import image_rotation_degrees, rotate_image_bytes

LOGGER = logging.getLogger(__name__)
DATA_LIVE_CAMERAS = f"{DOMAIN}_live_cameras"
DATA_LIVE_VIEW_REGISTERED = f"{DOMAIN}_live_view_registered"
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
    registry = hass.data.setdefault(DATA_LIVE_CAMERAS, {})
    if not hass.data.get(DATA_LIVE_VIEW_REGISTERED):
        hass.http.register_view(XHomeLiveMjpegView(registry))
        hass.data[DATA_LIVE_VIEW_REGISTERED] = True

    entities = [XHomeLiveCamera(coordinator, uid) for uid in coordinator.data.devices]
    for entity in entities:
        registry[(entry.entry_id, entity.uid, entity.stream_token)] = entity

    def cleanup_live_cameras() -> None:
        for entity in entities:
            registry.pop((entry.entry_id, entity.uid, entity.stream_token), None)

    entry.async_on_unload(cleanup_live_cameras)
    async_add_entities(entities)


class XHomeLiveCamera(XHomeEntity, Camera):
    """Live-stream camera backed by the native XHome P2P transport."""

    _attr_translation_key = "live_camera"
    _attr_should_poll = False
    _attr_content_type = "image/jpeg"
    _attr_frame_interval = 0.2

    def __init__(self, coordinator: XHomeDataUpdateCoordinator, uid: str) -> None:
        """Initialize the live camera entity."""

        Camera.__init__(self)
        XHomeEntity.__init__(self, coordinator, uid, "live_camera")
        self._last_live_jpeg: bytes | None = None
        self._stream_token = secrets.token_urlsafe(24)
        self._live_streams_started = 0
        self._live_frames = 0
        self._live_rotated_frames = 0
        self._live_rotation_failures = 0
        self._live_last_started_at: int | None = None
        self._live_last_frame_at: int | None = None
        self._live_last_error: str | None = None
        self._live_transport_stats: dict[str, Any] = {}

    @property
    def stream_token(self) -> str:
        """Return the opaque internal MJPEG stream token."""

        return self._stream_token

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
                "image_rotation": self._image_rotation(),
                "live_streams_started": self._live_streams_started,
                "live_frames": self._live_frames,
                "live_rotated_frames": self._live_rotated_frames,
                "live_rotation_failures": self._live_rotation_failures,
                "live_kcp_payloads": self._live_transport_stats.get("kcp_payloads"),
                "live_app_packets": self._live_transport_stats.get("app_packets"),
                "live_p2p_frames": self._live_transport_stats.get("frames"),
                "live_p2p_jpeg_frames": self._live_transport_stats.get("jpeg_frames"),
                "live_media_probe_error": self._live_transport_stats.get("error"),
                "live_last_started_at": self._live_last_started_at,
                "live_last_frame_at": self._live_last_frame_at,
                "live_last_error": self._live_last_error,
            }
        )
        if data is not None and data.device_id is not None:
            attrs["device_id"] = data.device_id
        return {key: value for key, value in attrs.items() if value is not None}

    async def stream_source(self) -> str | None:
        """Return the embedded MJPEG stream URL."""

        if self.device_data is None:
            return None
        return _native_mjpeg_url(self.hass, self.coordinator.config_entry.entry_id, self.uid, self._stream_token)

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
            loop.call_soon_threadsafe(self._handle_live_jpeg, frame_queue, frame.payload)

        def on_error(message: str) -> None:
            loop.call_soon_threadsafe(self._handle_live_error, message)

        def on_stats(stats: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(self._handle_live_transport_stats, stats)

        self._live_streams_started += 1
        self._live_frames = 0
        self._live_rotated_frames = 0
        self._live_rotation_failures = 0
        self._live_last_started_at = int(time.time())
        self._live_last_frame_at = None
        self._live_last_error = None
        self._live_transport_stats = {}
        self.async_write_ha_state()

        thread = Thread(
            target=_run_native_mjpeg_worker,
            args=(session, on_frame, on_error, on_stats, stop_event),
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
                    if self._live_frames == 0:
                        self._handle_live_error("Timed out waiting for first live JPEG frame")
                    if not thread.is_alive():
                        break
                    continue
                frame = self._rotate_stream_jpeg(frame)
                if frame is None:
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

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        """Return the latest live JPEG, falling back to latest event image."""

        if self._last_live_jpeg is not None:
            return self._rotate_jpeg(self._last_live_jpeg)
        image = await self.coordinator.async_get_latest_event_image(self.uid)
        if image is None:
            return None
        return self._rotate_jpeg(image)

    def _handle_live_jpeg(self, frame_queue: asyncio.Queue[bytes], frame: bytes) -> None:
        """Cache one live JPEG and queue it for the MJPEG response."""

        self._last_live_jpeg = frame
        self._live_frames += 1
        self._live_last_frame_at = int(time.time())
        self._live_last_error = None
        _replace_latest_frame(frame_queue, frame)
        self.async_write_ha_state()

    def _rotate_jpeg(self, image: bytes) -> bytes:
        """Apply the configured camera image rotation to JPEG bytes."""

        rotation = self._image_rotation()
        if rotation == 0:
            return image
        rotated = rotate_image_bytes(
            image,
            rotation,
            self._attr_content_type,
        )
        if rotated == image:
            self._live_rotation_failures += 1
        else:
            self._live_rotated_frames += 1
        return rotated

    def _rotate_stream_jpeg(self, image: bytes) -> bytes | None:
        """Apply rotation for the MJPEG stream, skipping failed rotated frames."""

        rotation = self._image_rotation()
        if rotation == 0:
            return image
        rotated = rotate_image_bytes(
            image,
            rotation,
            self._attr_content_type,
        )
        if rotated == image:
            self._live_rotation_failures += 1
            return None
        self._live_rotated_frames += 1
        return rotated

    def _image_rotation(self) -> int:
        """Return the configured camera image rotation."""

        return image_rotation_degrees(self.coordinator.config_entry.options)

    def _handle_live_error(self, message: str) -> None:
        """Store one native live stream error for diagnostics."""

        self._live_last_error = message
        self.async_write_ha_state()

    def _handle_live_transport_stats(self, stats: dict[str, Any]) -> None:
        """Store compact native media pipeline counters for diagnostics."""

        self._live_transport_stats = stats
        self.async_write_ha_state()


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


class XHomeLiveMjpegView(HomeAssistantView):
    """Internal MJPEG endpoint used as the camera stream source."""

    url = "/api/xhome/live/{entry_id}/{uid}/{token}.mjpeg"
    name = "api:xhome:live"
    requires_auth = False

    def __init__(self, registry: dict[tuple[str, str, str], XHomeLiveCamera]) -> None:
        """Initialize the internal stream view."""

        self._registry = registry

    async def get(self, request: web.Request, entry_id: str, uid: str, token: str) -> web.StreamResponse:
        """Serve one camera's native MJPEG stream."""

        camera = self._registry.get((entry_id, uid, token))
        if camera is None:
            raise web.HTTPNotFound()
        return await camera.handle_async_mjpeg_stream(request)


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


def _native_mjpeg_url(hass: HomeAssistant, entry_id: str, uid: str, token: str) -> str:
    """Return an absolute internal MJPEG URL for Home Assistant's stream worker."""

    return f"{get_url(hass, prefer_external=False)}/api/xhome/live/{entry_id}/{uid}/{token}.mjpeg"


def _run_native_mjpeg_worker(
    session: XHomeLiveStreamSession,
    on_frame: Any,
    on_error: Any,
    on_stats: Any,
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
                    commands = [frame.command for frame in native_frames]
                    raise RuntimeError(f"Native IoT session did not return any P2P relays; commands={commands}")
                LOGGER.info("XHome native live stream using relays: %s", relays)

                XHomeP2PRendezvousProbe(
                    uid=metadata.uid,
                    relays=relays,
                    direct_punch_enabled=True,
                ).run(
                    duration=MJPEG_STREAM_DURATION,
                    on_frame=on_frame,
                    on_stats=on_stats,
                    stop_event=stop_event,
                )
            finally:
                try:
                    transport.send_frame(metadata.stop_command)
                except Exception as err:  # noqa: BLE001
                    LOGGER.debug("XHome native live stop command failed: %s", err)
    except Exception as err:  # noqa: BLE001
        message = str(err)
        on_error(message)
        LOGGER.warning("XHome native live stream stopped: %s", message)
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
