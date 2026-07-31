"""Camera platform for the XHome Home Assistant integration."""

from __future__ import annotations

import asyncio
import json
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

from .api.live import ControlCommand, LiveAppMediaFrame, LiveSessionMetadata, MediaType
from .api.live_p2p import XHomeP2PRendezvousProbe
from .api.live_transport import XHomeLiveCloudTransport, encode_device_setting_payload, extract_p2p_servers
from .const import DOMAIN
from .coordinator import XHomeDataUpdateCoordinator, XHomeLiveStreamSession
from .entity import XHomeEntity
from .helpers import redact_uid
from .image import (
    image_rotation_degrees,
    is_decodable_jpeg,
    prepare_live_image_bytes,
    rotate_image_bytes,
)

LOGGER = logging.getLogger(__name__)
DATA_LIVE_CAMERAS = f"{DOMAIN}_live_cameras"
DATA_LIVE_VIEW_REGISTERED = f"{DOMAIN}_live_view_registered"
MJPEG_BOUNDARY = b"xhome"
MJPEG_STREAM_DURATION = 3600.0
MJPEG_FIRST_FRAME_TIMEOUT = 30.0
MJPEG_NEXT_FRAME_TIMEOUT = 15.0
MJPEG_VIEW_RECONNECT_INTERVAL = 20.0
MJPEG_VIEW_PROMOTE_TIMEOUT = 3.5
LIVE_STATE_WRITE_INTERVAL = 2.0
NATIVE_CONTROL_KEEPALIVE_INTERVAL = 12.0
NATIVE_CONTROL_READ_INTERVAL = 2.0
NATIVE_CONTROL_READ_DURATION = 0.005
NATIVE_CONTROL_POST_START_STATUS_COMMANDS = (
    ControlCommand.GET_BATTERY_LEVEL_REQ,
    ControlCommand.GET_DEVICE_RSSI_REQ,
    ControlCommand.GET_RESOLUTION_REQ,
)
NATIVE_CONTROL_POST_START_DEVICE_COMMANDS = (
    ControlCommand.DEVICE_SET_CMD_GET_DEVICE_ROTATE_REQ,
)


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
        hass.http.register_view(XHomeLiveMjpegViewerView(registry))
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
        self._live_invalid_jpeg_frames = 0
        self._live_rotation_edge_crop_pixels = 0
        self._live_last_started_at: int | None = None
        self._live_last_frame_at: int | None = None
        self._live_last_error: str | None = None
        self._live_mjpeg_clients_active = 0
        self._live_mjpeg_frames_written = 0
        self._live_mjpeg_last_end_reason: str | None = None
        self._live_mjpeg_stream_generation = 0
        self._live_last_state_write_at = 0.0
        self._live_startup_started_at = 0.0
        self._live_startup_timings_ms: dict[str, int] = {}

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
                "embedded_live_stream": True,
                "bridge": "embedded",
                "native_transport": "portable_p2p",
                "video_codec": "mjpeg",
                "audio_codec": "g711",
                "image_rotation": self._image_rotation(),
                "live_rotation_edge_crop_mode": "auto",
                "live_rotation_edge_crop_pixels": self._live_rotation_edge_crop_pixels,
                "live_streams_started": self._live_streams_started,
                "live_frames": self._live_frames,
                "live_rotated_frames": self._live_rotated_frames,
                "live_rotation_failures": self._live_rotation_failures,
                "live_invalid_jpeg_frames": self._live_invalid_jpeg_frames,
                "live_mjpeg_clients_active": self._live_mjpeg_clients_active,
                "live_mjpeg_frames_written": self._live_mjpeg_frames_written,
                "live_mjpeg_last_end_reason": self._live_mjpeg_last_end_reason,
                "live_mjpeg_view_path": _native_mjpeg_view_path(
                    self.coordinator.config_entry.entry_id,
                    self.uid,
                    self._stream_token,
                ),
                "live_mjpeg_view_reconnect_seconds": MJPEG_VIEW_RECONNECT_INTERVAL,
                "live_last_started_at": self._live_last_started_at,
                "live_last_frame_at": self._live_last_frame_at,
                "live_last_error": self._live_last_error,
                "live_startup_timings_ms": dict(self._live_startup_timings_ms),
            }
        )
        if data is not None and data.device_id is not None:
            attrs["device_id"] = data.device_id
        return {key: value for key, value in attrs.items() if value is not None}

    async def handle_async_mjpeg_stream(self, request: web.Request) -> web.StreamResponse:
        """Serve a native XHome live session as a Home Assistant MJPEG stream."""

        if self.device_data is None:
            raise HomeAssistantError("XHome device is unavailable")

        startup_started_at = time.monotonic()
        session = await self.coordinator.async_prepare_live_stream(self.uid)
        token_ready_ms = _elapsed_ms(startup_started_at)
        frame_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1)
        stop_event = Event()
        loop = asyncio.get_running_loop()

        def on_frame(frame: LiveAppMediaFrame) -> None:
            if frame.media_type != MediaType.JPEG_FRAME:
                return
            loop.call_soon_threadsafe(self._handle_live_jpeg, frame_queue, frame.payload)

        def on_error(message: str) -> None:
            loop.call_soon_threadsafe(self._handle_live_error, message)

        def on_timing(name: str, elapsed_ms: int) -> None:
            loop.call_soon_threadsafe(self._record_live_startup_timing, name, elapsed_ms)

        self._live_streams_started += 1
        self._live_mjpeg_stream_generation += 1
        stream_generation = self._live_mjpeg_stream_generation
        self._live_startup_started_at = startup_started_at
        self._live_startup_timings_ms = {"token_ready": token_ready_ms}
        self._live_frames = 0
        self._live_rotated_frames = 0
        self._live_rotation_failures = 0
        self._live_invalid_jpeg_frames = 0
        self._live_rotation_edge_crop_pixels = 0
        self._live_last_started_at = int(time.time())
        self._live_last_frame_at = None
        self._live_last_error = None
        self._live_mjpeg_clients_active += 1
        self._live_mjpeg_frames_written = 0
        self._live_mjpeg_last_end_reason = None
        self._write_live_state(force=True)

        thread = Thread(
            target=_run_native_mjpeg_worker,
            args=(
                session,
                on_frame,
                on_error,
                stop_event,
                startup_started_at,
                on_timing,
                lambda generation=stream_generation: self._live_mjpeg_stream_generation == generation,
            ),
            name=f"xhome-live-{redact_uid(self.uid)}",
            daemon=True,
        )
        thread.start()
        end_reason = "completed"

        response = web.StreamResponse(
            status=200,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "X-Accel-Buffering": "no",
                "Content-Encoding": "identity",
                "Content-Type": f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY.decode('ascii')}",
            },
        )
        await response.prepare(request)

        try:
            while not stop_event.is_set():
                try:
                    frame_timeout = MJPEG_FIRST_FRAME_TIMEOUT if self._live_frames == 0 else MJPEG_NEXT_FRAME_TIMEOUT
                    frame = await asyncio.wait_for(frame_queue.get(), timeout=frame_timeout)
                except TimeoutError:
                    if self._live_frames == 0:
                        self._handle_live_error("Timed out waiting for first live JPEG frame")
                        end_reason = "first_frame_timeout"
                    else:
                        self._handle_live_error("Timed out waiting for next live JPEG frame")
                        end_reason = "next_frame_timeout"
                        break
                    if not thread.is_alive():
                        end_reason = "worker_stopped_before_frame"
                        break
                    continue
                frame, frame_status, edge_crop_pixels = await self.hass.async_add_executor_job(
                    self._prepare_stream_jpeg,
                    frame,
                    self._live_rotation_edge_crop_pixels,
                )
                self._live_rotation_edge_crop_pixels = max(self._live_rotation_edge_crop_pixels, edge_crop_pixels)
                if frame_status == "invalid":
                    self._live_invalid_jpeg_frames += 1
                elif frame_status == "rotation_failed":
                    self._live_rotation_failures += 1
                elif frame_status == "rotated":
                    self._live_rotated_frames += 1
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
                if self._live_mjpeg_frames_written == 0:
                    self._record_live_startup_timing("first_mjpeg_frame_written")
                self._live_mjpeg_frames_written += 1
                self._write_live_state()
        except asyncio.CancelledError:
            end_reason = "cancelled"
            raise
        except (ConnectionResetError, BrokenPipeError) as err:
            end_reason = err.__class__.__name__
        except Exception as err:
            end_reason = f"{err.__class__.__name__}: {err}"
            raise
        finally:
            stop_event.set()
            await self.hass.async_add_executor_job(thread.join, 2)
            self._live_mjpeg_clients_active = max(0, self._live_mjpeg_clients_active - 1)
            self._live_mjpeg_last_end_reason = end_reason
            self._write_live_state(force=True)

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
        self._write_live_state()

    def _record_live_startup_timing(self, name: str, elapsed_ms: int | None = None) -> None:
        """Record one live startup milestone in milliseconds."""

        if name in self._live_startup_timings_ms:
            return
        if elapsed_ms is None:
            elapsed_ms = _elapsed_ms(self._live_startup_started_at)
        self._live_startup_timings_ms[name] = elapsed_ms
        self._write_live_state(force=True)

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

    def _prepare_stream_jpeg(
        self,
        image: bytes,
        minimum_edge_crop_pixels: int,
    ) -> tuple[bytes | None, str, int]:
        """Prepare one MJPEG frame for streaming."""

        rotation = self._image_rotation()
        if rotation == 0:
            if not is_decodable_jpeg(image):
                return None, "invalid", 0
            return image, "original", 0
        rotated, edge_crop_pixels = prepare_live_image_bytes(
            image,
            rotation,
            self._attr_content_type,
            minimum_edge_crop_pixels=minimum_edge_crop_pixels,
        )
        if rotated is None:
            return None, "invalid", edge_crop_pixels
        if rotated == image:
            return None, "rotation_failed", edge_crop_pixels
        return rotated, "rotated", edge_crop_pixels

    def _image_rotation(self) -> int:
        """Return the configured camera image rotation."""

        return image_rotation_degrees(self.coordinator.config_entry.options)

    def _handle_live_error(self, message: str) -> None:
        """Store one native live stream error for diagnostics."""

        self._live_last_error = message
        self._write_live_state(force=True)

    def _write_live_state(self, *, force: bool = False) -> None:
        """Write live diagnostics to HA state at a throttled cadence."""

        now = time.monotonic()
        if not force and now - self._live_last_state_write_at < LIVE_STATE_WRITE_INTERVAL:
            return
        self._live_last_state_write_at = now
        self.async_write_ha_state()


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


class XHomeLiveMjpegViewerView(HomeAssistantView):
    """Small browser-safe viewer for the tokenized MJPEG endpoint."""

    url = "/api/xhome/live-view/{entry_id}/{uid}/{token}"
    name = "api:xhome:live_view"
    requires_auth = False

    def __init__(self, registry: dict[tuple[str, str, str], XHomeLiveCamera]) -> None:
        """Initialize the internal viewer view."""

        self._registry = registry

    async def get(self, request: web.Request, entry_id: str, uid: str, token: str) -> web.Response:
        """Serve a reconnecting HTML wrapper around one camera's MJPEG stream."""

        if self._registry.get((entry_id, uid, token)) is None:
            raise web.HTTPNotFound()
        return web.Response(
            text=_native_mjpeg_view_html(_native_mjpeg_path(entry_id, uid, token)),
            content_type="text/html",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "X-Frame-Options": "SAMEORIGIN",
            },
        )


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


def _elapsed_ms(started_at: float) -> int:
    """Return elapsed monotonic time in milliseconds."""

    if started_at <= 0:
        return 0
    return int((time.monotonic() - started_at) * 1000)


def _native_mjpeg_path(entry_id: str, uid: str, token: str) -> str:
    """Return the tokenized internal MJPEG path for direct debug access."""

    return f"/api/xhome/live/{entry_id}/{uid}/{token}.mjpeg"


def _native_mjpeg_view_path(entry_id: str, uid: str, token: str) -> str:
    """Return the tokenized browser-safe MJPEG viewer path."""

    return f"/api/xhome/live-view/{entry_id}/{uid}/{token}"


def _native_mjpeg_view_html(stream_path: str) -> str:
    """Return a minimal viewer that reconnects before browser MJPEG cancellation."""

    reconnect_ms = int(MJPEG_VIEW_RECONNECT_INTERVAL * 1000)
    promote_ms = int(MJPEG_VIEW_PROMOTE_TIMEOUT * 1000)
    stream_path_json = json.dumps(stream_path)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>XHome Live Camera</title>
<style>
html,
body {{
  margin: 0;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: #050505;
}}
.stream {{
  position: fixed;
  inset: 0;
  width: 100vw;
  height: 100vh;
  object-fit: contain;
  background: #050505;
}}
.pending {{
  opacity: 0;
}}
</style>
</head>
<body>
<script>
const streamPath = {stream_path_json};
const reconnectMs = {reconnect_ms};
const promoteMs = {promote_ms};
let activeImage = null;
let reconnectTimer = null;
let streamIndex = 0;

function streamUrl() {{
  const separator = streamPath.indexOf("?") === -1 ? "?" : "&";
  return streamPath + separator + "viewer=" + Date.now() + "-" + streamIndex++;
}}

function clearImage(image) {{
  image.removeAttribute("src");
  image.remove();
}}

function promote(image) {{
  if (image.dataset.promoted === "1") {{
    return;
  }}
  image.dataset.promoted = "1";
  image.className = "stream";
  const previous = activeImage;
  activeImage = image;
  if (previous && previous !== image) {{
    window.setTimeout(() => clearImage(previous), 250);
  }}
  window.clearTimeout(reconnectTimer);
  reconnectTimer = window.setTimeout(openStream, reconnectMs);
}}

function openStream() {{
  const image = new Image();
  image.className = activeImage ? "stream pending" : "stream";
  image.alt = "";
  image.decoding = "async";
  image.onload = () => promote(image);
  image.onerror = () => {{
    if (image.dataset.promoted !== "1") {{
      clearImage(image);
    }}
    window.clearTimeout(reconnectTimer);
    reconnectTimer = window.setTimeout(openStream, 1000);
  }};
  document.body.appendChild(image);
  image.src = streamUrl();
  window.setTimeout(() => promote(image), promoteMs);
}}

window.addEventListener("pagehide", () => {{
  window.clearTimeout(reconnectTimer);
  for (const image of document.querySelectorAll("img.stream")) {{
    image.removeAttribute("src");
  }}
}});

openStream();
</script>
</body>
</html>
"""


def _run_native_mjpeg_worker(
    session: XHomeLiveStreamSession,
    on_frame: Any,
    on_error: Any,
    stop_event: Event,
    startup_started_at: float,
    on_timing: Any | None = None,
    should_send_stop: Any | None = None,
) -> None:
    """Run one blocking native live session for the MJPEG response."""

    metadata = LiveSessionMetadata(
        uid=session.uid,
        token=session.token,
        native_iot_host=session.native_iot_host,
        device_id=session.device_id,
        model=session.model,
    )

    def mark_timing(name: str) -> None:
        if on_timing is not None:
            on_timing(name, _elapsed_ms(startup_started_at))

    try:
        with XHomeLiveCloudTransport(metadata, verify_tls=False) as transport:
            mark_timing("native_connected")
            try:
                native_control = _NativeLiveControlKeeper(transport, metadata)
                transport.login()
                mark_timing("native_login_sent")
                native_frames = transport.read_available(duration=1.0)
                mark_timing("native_initial_read_done")
                transport.send_frame(metadata.start_command)
                mark_timing("av_start_sent")
                native_frames.extend(transport.read_available(duration=3.0))
                mark_timing("native_post_start_read_done")
                if not extract_p2p_servers(native_frames):
                    native_frames.extend(transport.read_available(duration=5.0))
                    mark_timing("native_relay_fallback_done")

                relays = _unique_p2p_relays(extract_p2p_servers(native_frames))
                if not relays:
                    commands = [frame.command for frame in native_frames]
                    raise RuntimeError(f"Native IoT session did not return any P2P relays; commands={commands}")
                mark_timing("p2p_relays_ready")
                LOGGER.info("XHome native live stream using relays: %s", relays)

                first_media_timing_sent = False

                def on_native_frame(frame: LiveAppMediaFrame) -> None:
                    nonlocal first_media_timing_sent
                    if not first_media_timing_sent:
                        first_media_timing_sent = True
                        mark_timing("first_media_frame")
                    if frame.media_type == MediaType.JPEG_FRAME:
                        mark_timing("first_jpeg_frame")
                    native_control.refresh_after_first_frame()
                    on_frame(frame)

                def on_native_stats(_stats: dict[str, Any]) -> None:
                    native_control.tick()

                XHomeP2PRendezvousProbe(
                    uid=metadata.uid,
                    relays=relays,
                    direct_punch_enabled=True,
                )
                mark_timing("p2p_probe_started")
                probe.run(
                    duration=MJPEG_STREAM_DURATION,
                    on_frame=on_native_frame,
                    on_stats=on_native_stats,
                    stop_event=stop_event,
                )
            finally:
                if should_send_stop is None or should_send_stop():
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


class _NativeLiveControlKeeper:
    """Keep the native IoT control channel active while UDP media is flowing."""

    def __init__(self, transport: XHomeLiveCloudTransport, metadata: LiveSessionMetadata) -> None:
        self._transport = transport
        self._metadata = metadata
        self._first_frame_refresh_sent = False
        self._next_keepalive = time.monotonic() + NATIVE_CONTROL_KEEPALIVE_INTERVAL
        self._next_read = time.monotonic() + NATIVE_CONTROL_READ_INTERVAL

    def refresh_after_first_frame(self) -> None:
        """Mirror the native app's post-media-start control traffic."""

        if self._first_frame_refresh_sent:
            return
        self._first_frame_refresh_sent = True
        self._send_post_start_status_probes()
        self._send_post_start_device_setting_probes()
        now = time.monotonic()
        self._next_keepalive = now + NATIVE_CONTROL_KEEPALIVE_INTERVAL
        self._next_read = now + NATIVE_CONTROL_READ_INTERVAL

    def tick(self) -> None:
        """Read pending control responses and periodically refresh AV start."""

        now = time.monotonic()
        if now >= self._next_read:
            self._read_pending(duration=NATIVE_CONTROL_READ_DURATION)
            self._next_read = now + NATIVE_CONTROL_READ_INTERVAL
        if now >= self._next_keepalive:
            self._send_keepalive()
            self._next_keepalive = now + NATIVE_CONTROL_KEEPALIVE_INTERVAL
            self._next_read = now + NATIVE_CONTROL_READ_INTERVAL

    def _send_post_start_status_probes(self) -> None:
        for command in NATIVE_CONTROL_POST_START_STATUS_COMMANDS:
            self._send_control_frame(command)
        self._read_pending()

    def _send_post_start_device_setting_probes(self) -> None:
        for command in NATIVE_CONTROL_POST_START_DEVICE_COMMANDS:
            payload = encode_device_setting_payload(command)
            self._send_control_frame(ControlCommand.DEVICE_SETTING_COMB_CMD, payload)
        self._read_pending()

    def _send_keepalive(self) -> None:
        self._send_control_frame(ControlCommand.GET_BATTERY_LEVEL_REQ)
        self._read_pending()

    def _send_control_frame(self, command: int, payload: bytes = b"") -> None:
        try:
            self._transport.send_frame(command, payload)
        except Exception as err:  # noqa: BLE001
            LOGGER.debug("XHome native live control send failed: %s", err)

    def _read_pending(self, *, duration: float = 0.05) -> None:
        try:
            self._transport.read_available(duration=duration)
        except Exception as err:  # noqa: BLE001
            LOGGER.debug("XHome native live control read failed: %s", err)


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
