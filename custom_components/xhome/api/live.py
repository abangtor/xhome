"""Helpers for XHome native live-stream sidecars."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any


MEDIA_HEADER_BYTES = 40
APP_MEDIA_HEADER_BYTES = 20


class CallbackType(IntEnum):
    """Callback type values used by ``IVIEWSAVAPIs.AVAPISCallback``."""

    IVIEWS_CONNECTION = 0
    IVIEWS_LOGIN = 1
    IVIEWS_DATA = 2
    P2P_CONNECTION = 3
    P2P_DATA = 4
    LOG = 5


class ConnectionStatus(IntEnum):
    """Connection status values observed in the Android wrapper."""

    SUCCESS = 0
    FAILED = -1
    DISCONNECTED = -2
    P2P_DISCONNECTED = -100


class ControlCommand(IntEnum):
    """Control commands needed for receive-only live AV."""

    LAN_GET_AV_DATA_RESP = 8
    P2P_READY_RESP = 9
    AV_START_REQ = 20
    AV_STOP_REQ = 21
    AV_START_RESP = 22
    AV_STOP_RESP = 23
    AV_DISPLACED_RESP = 25


class MediaType(IntEnum):
    """Media frame type values embedded in the 40-byte media header."""

    H264_P_FRAME = 160
    H264_I_FRAME = 161
    H264_B_FRAME = 162
    G711_AUDIO = 164
    JPEG_FRAME = 165


H264_MEDIA_TYPES = {
    MediaType.H264_P_FRAME,
    MediaType.H264_I_FRAME,
    MediaType.H264_B_FRAME,
}


@dataclass(frozen=True)
class LiveSessionMetadata:
    """Minimum metadata needed by the native live-stream sidecar."""

    uid: str
    token: str
    native_iot_host: str
    device_id: int | None = None
    model: str | int | None = None
    start_command: int = int(ControlCommand.AV_START_REQ)
    stop_command: int = int(ControlCommand.AV_STOP_REQ)
    video_codec: str = "h264"
    audio_codec: str = "g711"
    media_header_bytes: int = MEDIA_HEADER_BYTES
    token_payload: dict[str, Any] | None = None

    def as_bridge_payload(self) -> dict[str, Any]:
        """Return the JSON object sent to a native helper on startup."""

        return {
            "uid": self.uid,
            "token": self.token,
            "native_iot_host": self.native_iot_host,
            "device_id": self.device_id,
            "model": self.model,
            "start_command": self.start_command,
            "stop_command": self.stop_command,
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            "media_header_bytes": self.media_header_bytes,
        }


@dataclass(frozen=True)
class LiveCallback:
    """One callback emitted by a native helper."""

    callback_type: int
    command: int
    status: int
    payload: bytes

    @property
    def is_ready(self) -> bool:
        """Return whether this callback means the stream may be started."""

        return self.callback_type == CallbackType.P2P_CONNECTION and self.status == ConnectionStatus.SUCCESS

    @property
    def is_media_payload(self) -> bool:
        """Return whether this callback can carry framed live-media bytes."""

        return self.callback_type in {CallbackType.IVIEWS_DATA, CallbackType.P2P_DATA}


@dataclass(frozen=True)
class MediaFrame:
    """One parsed XHome media frame."""

    media_type: MediaType
    timestamp: int
    sample_rate: int
    payload: bytes
    header: bytes

    @property
    def is_h264(self) -> bool:
        return self.media_type in H264_MEDIA_TYPES

    @property
    def is_g711(self) -> bool:
        return self.media_type == MediaType.G711_AUDIO

    @property
    def is_jpeg(self) -> bool:
        return self.media_type == MediaType.JPEG_FRAME


@dataclass(frozen=True)
class LiveAppMediaPacket:
    """One command-8 media fragment from the portable P2P/KCP stream."""

    command: int
    declared_length: int
    media_type: MediaType
    sequence: int
    fragment_index: int
    fragment_flag: int
    payload: bytes
    header: bytes

    @property
    def starts_frame(self) -> bool:
        return self.fragment_flag == 1

    @property
    def continues_frame(self) -> bool:
        return self.fragment_flag == 0

    @property
    def ends_frame(self) -> bool:
        return self.fragment_flag == 2


@dataclass(frozen=True)
class LiveAppMediaFrame:
    """One assembled media frame from command-8 P2P fragments."""

    media_type: MediaType
    payload: bytes

    @property
    def is_h264(self) -> bool:
        return self.media_type in H264_MEDIA_TYPES

    @property
    def is_g711(self) -> bool:
        return self.media_type == MediaType.G711_AUDIO

    @property
    def is_jpeg(self) -> bool:
        return self.media_type == MediaType.JPEG_FRAME


def live_session_from_token_payload(
    *,
    uid: str,
    native_iot_host: str,
    payload: dict[str, Any],
    device_id: int | None = None,
    model: str | int | None = None,
) -> LiveSessionMetadata:
    """Build live-session metadata from a REST token response payload."""

    token = payload.get("token")
    if not token:
        raise ValueError("Live-token payload does not contain token")
    return LiveSessionMetadata(
        uid=uid,
        token=str(token),
        native_iot_host=native_iot_host,
        device_id=device_id,
        model=model,
        token_payload=dict(payload),
    )


def parse_media_frame(data: bytes, *, header_bytes: int = MEDIA_HEADER_BYTES) -> MediaFrame:
    """Parse one native XHome media record."""

    if len(data) < header_bytes:
        raise ValueError(f"Media frame is too short: {len(data)} < {header_bytes}")
    if header_bytes < 4:
        raise ValueError("Media header must include at least 4 bytes")

    try:
        media_type = MediaType(data[3])
    except ValueError as exc:
        raise ValueError(f"Unknown XHome media type: {data[3]}") from exc

    timestamp = int.from_bytes(data[12:20], "little", signed=False) if len(data) >= 20 else 0
    sample_rate = int.from_bytes(data[28:32], "little", signed=False) if len(data) >= 32 else 0
    return MediaFrame(
        media_type=media_type,
        timestamp=timestamp,
        sample_rate=sample_rate,
        payload=data[header_bytes:],
        header=data[:header_bytes],
    )


def parse_live_app_media_packet(data: bytes) -> LiveAppMediaPacket:
    """Parse one command-8 media fragment from the P2P/KCP stream.

    Captured Android traffic uses a 20-byte application header inside KCP:

    - bytes 0..3: little-endian command, usually ``8``
    - bytes 4..7: payload length after these first 8 bytes
    - byte 11: media type, e.g. ``165`` for JPEG
    - byte 13: rolling sequence id
    - byte 14: fragment index within the current frame
    - byte 15: fragment flag, ``1`` start, ``0`` middle, ``2`` end
    - bytes 16..19: fragment payload length
    """

    if len(data) < APP_MEDIA_HEADER_BYTES:
        raise ValueError(f"Live app media packet is too short: {len(data)} < {APP_MEDIA_HEADER_BYTES}")
    command = int.from_bytes(data[:4], "little", signed=False)
    declared_length = int.from_bytes(data[4:8], "little", signed=False)
    try:
        media_type = MediaType(data[11])
    except ValueError as exc:
        raise ValueError(f"Unknown XHome app media type: {data[11]}") from exc
    payload_length = int.from_bytes(data[16:20], "little", signed=False)
    return LiveAppMediaPacket(
        command=command,
        declared_length=declared_length,
        media_type=media_type,
        sequence=data[13],
        fragment_index=data[14],
        fragment_flag=data[15],
        payload=data[APP_MEDIA_HEADER_BYTES : APP_MEDIA_HEADER_BYTES + payload_length],
        header=data[:APP_MEDIA_HEADER_BYTES],
    )


class LiveAppMediaAssembler:
    """Assemble command-8 P2P media fragments into full frames."""

    def __init__(self) -> None:
        self._media_type: MediaType | None = None
        self._chunks = bytearray()

    def feed(self, packet: LiveAppMediaPacket) -> LiveAppMediaFrame | None:
        """Feed one parsed packet and return a frame when an end fragment arrives."""

        if packet.starts_frame:
            self._media_type = packet.media_type
            self._chunks = bytearray(packet.payload)
            return None
        if self._media_type is None:
            return None
        if packet.media_type != self._media_type:
            self._media_type = packet.media_type
            self._chunks = bytearray(packet.payload)
            return None
        self._chunks.extend(packet.payload)
        if packet.ends_frame:
            frame = LiveAppMediaFrame(media_type=packet.media_type, payload=bytes(self._chunks))
            self._media_type = None
            self._chunks = bytearray()
            return frame
        return None


def callback_to_media_frame(callback: LiveCallback, *, header_bytes: int = MEDIA_HEADER_BYTES) -> MediaFrame | None:
    """Return a media frame from a live callback, or ``None`` for non-media callbacks."""

    if not callback.is_media_payload:
        return None
    if callback.command not in {ControlCommand.LAN_GET_AV_DATA_RESP, 0}:
        return None
    if not callback.payload:
        return None
    return parse_media_frame(callback.payload, header_bytes=header_bytes)
