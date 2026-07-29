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

from .api.live import ControlCommand, LiveAppMediaFrame, LiveSessionMetadata, MediaType
from .api.live_p2p import XHomeP2PRendezvousProbe
from .api.live_transport import XHomeLiveCloudTransport, extract_p2p_servers
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
LIVE_STATE_WRITE_INTERVAL = 2.0
NATIVE_CONTROL_KEEPALIVE_INTERVAL = 60.0
NATIVE_CONTROL_POST_START_STATUS_COMMANDS = (
    ControlCommand.GET_BATTERY_LEVEL_REQ,
    ControlCommand.GET_DEVICE_RSSI_REQ,
    ControlCommand.GET_RESOLUTION_REQ,
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
        self._live_transport_stats: dict[str, Any] = {}
        self._live_last_state_write_at = 0.0

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
        sent = self._live_transport_stats.get("sent") or {}
        selected_peer = self._live_transport_stats.get("selected_peer") or {}
        attrs.update(
            {
                "embedded_live_stream": True,
                "bridge": "embedded",
                "native_transport": "portable_p2p",
                "native_media_header_bytes": 40,
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
                "live_p2p_udp_packets": self._live_transport_stats.get("udp_packets"),
                "live_p2p_kcp_ack_datagrams": self._live_transport_stats.get("kcp_ack_datagrams"),
                "live_p2p_kcp_ack_segments": self._live_transport_stats.get("kcp_ack_segments"),
                "live_p2p_raw_kcp_prefixes": self._live_transport_stats.get("raw_kcp_prefixes"),
                "live_p2p_raw_channel_kcp_segments": self._live_transport_stats.get("raw_channel_kcp_segments"),
                "live_p2p_raw_channel_kcp_default_prefixes": self._live_transport_stats.get(
                    "raw_channel_kcp_default_prefixes"
                ),
                "live_p2p_raw_channel_kcp_missing_prefixes": self._live_transport_stats.get(
                    "raw_channel_kcp_missing_prefixes"
                ),
                "live_p2p_raw_channel_kcp_invalid_segments": self._live_transport_stats.get(
                    "raw_channel_kcp_invalid_segments"
                ),
                "live_kcp_payloads": self._live_transport_stats.get("kcp_payloads"),
                "live_app_packets": self._live_transport_stats.get("app_packets"),
                "live_p2p_frames": self._live_transport_stats.get("frames"),
                "live_p2p_jpeg_frames": self._live_transport_stats.get("jpeg_frames"),
                "live_p2p_packets": self._live_transport_stats.get("packets"),
                "live_p2p_candidate_count": self._live_transport_stats.get("candidate_count"),
                "live_p2p_loop_ticks": self._live_transport_stats.get("loop_ticks"),
                "live_p2p_selected_peer": _peer_label(selected_peer),
                "live_p2p_sent_heartbeats": sent.get("heartbeat"),
                "live_p2p_sent_direct_touches": sent.get("direct_touch_channel4"),
                "live_p2p_sent_relay_info": sent.get("relay_info"),
                "live_p2p_sent_relay_touches": sent.get("relay_touch_channel4"),
                "live_p2p_last_packet_at": self._live_transport_stats.get("last_packet_at"),
                "live_p2p_last_kcp_packet_at": self._live_transport_stats.get("last_kcp_packet_at"),
                "live_p2p_last_payload_at": self._live_transport_stats.get("last_payload_at"),
                "live_p2p_last_probe_frame_at": self._live_transport_stats.get("last_frame_at"),
                "live_media_probe_error": self._live_transport_stats.get("error"),
                "live_native_control_start_refreshes": self._live_transport_stats.get(
                    "native_control_start_refreshes"
                ),
                "live_native_control_keepalives": self._live_transport_stats.get("native_control_keepalives"),
                "live_native_control_status_probes": self._live_transport_stats.get(
                    "native_control_status_probes"
                ),
                "live_native_control_frames": self._live_transport_stats.get("native_control_frames"),
                "live_native_control_last_command": self._live_transport_stats.get(
                    "native_control_last_command"
                ),
                "live_native_control_last_sent_at": self._live_transport_stats.get("native_control_last_sent_at"),
                "live_native_control_last_read_at": self._live_transport_stats.get("native_control_last_read_at"),
                "live_native_control_last_error": self._live_transport_stats.get("native_control_last_error"),
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
        self._live_invalid_jpeg_frames = 0
        self._live_rotation_edge_crop_pixels = 0
        self._live_last_started_at = int(time.time())
        self._live_last_frame_at = None
        self._live_last_error = None
        self._live_transport_stats = {}
        self._write_live_state(force=True)

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
                    frame_timeout = MJPEG_FIRST_FRAME_TIMEOUT if self._live_frames == 0 else MJPEG_NEXT_FRAME_TIMEOUT
                    frame = await asyncio.wait_for(frame_queue.get(), timeout=frame_timeout)
                except TimeoutError:
                    if self._live_frames == 0:
                        self._handle_live_error("Timed out waiting for first live JPEG frame")
                    else:
                        self._handle_live_error("Timed out waiting for next live JPEG frame")
                        break
                    if not thread.is_alive():
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
        self._write_live_state()

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

    def _handle_live_transport_stats(self, stats: dict[str, Any]) -> None:
        """Store compact native media pipeline counters for diagnostics."""

        self._live_transport_stats = stats
        self._write_live_state()

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


def _peer_label(peer: Any) -> str | None:
    """Return a compact peer label for live P2P diagnostics."""

    if not isinstance(peer, dict) or "host" not in peer or "port" not in peer:
        return None
    return f"{peer['host']}:{peer['port']}"


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
                native_control = _NativeLiveControlKeeper(transport, metadata)
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

                def on_native_frame(frame: LiveAppMediaFrame) -> None:
                    native_control.refresh_after_first_frame()
                    on_frame(frame)

                def on_native_stats(stats: dict[str, Any]) -> None:
                    native_control.tick()
                    stats.update(native_control.as_dict())
                    on_stats(stats)

                XHomeP2PRendezvousProbe(
                    uid=metadata.uid,
                    relays=relays,
                    direct_punch_enabled=True,
                ).run(
                    duration=MJPEG_STREAM_DURATION,
                    on_frame=on_native_frame,
                    on_stats=on_native_stats,
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


class _NativeLiveControlKeeper:
    """Keep the native IoT control channel active while UDP media is flowing."""

    def __init__(self, transport: XHomeLiveCloudTransport, metadata: LiveSessionMetadata) -> None:
        self._transport = transport
        self._metadata = metadata
        self._first_frame_refresh_sent = False
        self._next_keepalive = time.monotonic() + NATIVE_CONTROL_KEEPALIVE_INTERVAL
        self._start_refreshes = 0
        self._keepalives = 0
        self._status_probes = 0
        self._frames = 0
        self._last_command: int | None = None
        self._last_sent_at: int | None = None
        self._last_read_at: int | None = None
        self._last_error: str | None = None

    def refresh_after_first_frame(self) -> None:
        """Mirror the native app's post-media-start control traffic."""

        if self._first_frame_refresh_sent:
            return
        self._first_frame_refresh_sent = True
        self._send_start_refresh()
        self._send_post_start_status_probes()
        self._next_keepalive = time.monotonic() + NATIVE_CONTROL_KEEPALIVE_INTERVAL

    def tick(self) -> None:
        """Read pending control responses and periodically refresh AV start."""

        self._read_pending()
        now = time.monotonic()
        if now < self._next_keepalive:
            return
        self._send_keepalive()
        self._next_keepalive = now + NATIVE_CONTROL_KEEPALIVE_INTERVAL

    def as_dict(self) -> dict[str, Any]:
        """Return compact diagnostics for camera attributes."""

        return {
            "native_control_start_refreshes": self._start_refreshes,
            "native_control_keepalives": self._keepalives,
            "native_control_status_probes": self._status_probes,
            "native_control_frames": self._frames,
            "native_control_last_command": self._last_command,
            "native_control_last_sent_at": self._last_sent_at,
            "native_control_last_read_at": self._last_read_at,
            "native_control_last_error": self._last_error,
        }

    def _send_start_refresh(self) -> None:
        if self._send_control_frame(self._metadata.start_command):
            self._start_refreshes += 1
        self._read_pending()

    def _send_post_start_status_probes(self) -> None:
        for command in NATIVE_CONTROL_POST_START_STATUS_COMMANDS:
            if self._send_control_frame(command):
                self._status_probes += 1
        self._read_pending()

    def _send_keepalive(self) -> None:
        if self._send_control_frame(ControlCommand.GET_BATTERY_LEVEL_REQ):
            self._keepalives += 1
        self._read_pending()

    def _send_control_frame(self, command: int) -> bool:
        try:
            self._transport.send_frame(command)
        except Exception as err:  # noqa: BLE001
            self._last_error = str(err)
            return False
        self._last_command = int(command)
        self._last_sent_at = int(time.time())
        return True

    def _read_pending(self) -> None:
        try:
            frames = self._transport.read_available(duration=0.05)
        except Exception as err:  # noqa: BLE001
            self._last_error = str(err)
            return
        if not frames:
            return
        self._frames += len(frames)
        self._last_read_at = int(time.time())


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
