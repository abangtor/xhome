"""Portable pieces of XHome's native live-stream transport."""

from __future__ import annotations

import json
import socket
import ssl
import time
from dataclasses import dataclass
from typing import Any

from .live import ControlCommand, LiveSessionMetadata

NATIVE_IOT_PORT = 11201
LIVE_LOGIN_COMMAND = 10001
FRAME_HEADER_BYTES = 8


@dataclass(frozen=True)
class NativeFrame:
    """One frame from the XHome native IoT TLS socket."""

    command: int
    payload: bytes

    @property
    def text(self) -> str:
        return self.payload.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.text)


def encode_native_frame(command: int, payload: bytes = b"") -> bytes:
    """Encode the native TLS command frame used by ``IVIEWSClient::send``.

    Native layout, recovered from ``libIVIEWSAVAPIs.so``:

    - bytes 0..1: little-endian uint16 command
    - bytes 2..3: unused/padding
    - bytes 4..7: little-endian uint32 payload length
    - bytes 8..: payload
    """

    if command < 0 or command > 0xFFFF:
        raise ValueError(f"Command out of uint16 range: {command}")
    if len(payload) > 0xFFFFFFFF:
        raise ValueError("Payload is too large")
    return command.to_bytes(2, "little") + b"\x00\x00" + len(payload).to_bytes(4, "little") + payload


def decode_native_frame_header(header: bytes) -> tuple[int, int]:
    """Decode one native TLS frame header."""

    if len(header) != FRAME_HEADER_BYTES:
        raise ValueError(f"Expected {FRAME_HEADER_BYTES} header bytes, got {len(header)}")
    command = int.from_bytes(header[:2], "little", signed=False)
    payload_len = int.from_bytes(header[4:8], "little", signed=False)
    return command, payload_len


class XHomeLiveCloudTransport:
    """Portable implementation of the native IoT TLS login/control phase."""

    def __init__(
        self,
        metadata: LiveSessionMetadata,
        *,
        port: int = NATIVE_IOT_PORT,
        timeout: float = 10.0,
        verify_tls: bool = True,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self.metadata = metadata
        self.port = port
        self.timeout = timeout
        self.ssl_context = ssl_context or make_ssl_context(verify_tls=verify_tls)
        self._socket: ssl.SSLSocket | None = None

    def __enter__(self) -> XHomeLiveCloudTransport:
        self.connect()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def connect(self) -> None:
        """Open the native IoT TLS socket."""

        raw_socket = socket.create_connection((self.metadata.native_iot_host, self.port), timeout=self.timeout)
        raw_socket.settimeout(self.timeout)
        self._socket = self.ssl_context.wrap_socket(raw_socket, server_hostname=self.metadata.native_iot_host)

    def close(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    def login(self) -> None:
        """Send the native live-login command."""

        payload = json.dumps(
            {"UID": self.metadata.uid, "token": self.metadata.token},
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_frame(LIVE_LOGIN_COMMAND, payload)

    def send_frame(self, command: int, payload: bytes = b"") -> None:
        sock = self.require_socket()
        sock.sendall(encode_native_frame(command, payload))

    def read_frame(self) -> NativeFrame:
        sock = self.require_socket()
        header = read_exact(sock, FRAME_HEADER_BYTES)
        command, payload_len = decode_native_frame_header(header)
        payload = read_exact(sock, payload_len) if payload_len else b""
        return NativeFrame(command=command, payload=payload)

    def read_available(self, *, duration: float) -> list[NativeFrame]:
        """Read frames until timeout/duration expires."""

        frames: list[NativeFrame] = []
        deadline = time.monotonic() + duration
        sock = self.require_socket()
        old_timeout = sock.gettimeout()
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return frames
                sock.settimeout(min(self.timeout, remaining))
                try:
                    frames.append(self.read_frame())
                except TimeoutError:
                    return frames
                except socket.timeout:
                    return frames
        finally:
            sock.settimeout(old_timeout)

    def require_socket(self) -> ssl.SSLSocket:
        if self._socket is None:
            raise RuntimeError("Native IoT socket is not connected")
        return self._socket


def read_exact(sock: socket.socket, size: int) -> bytes:
    """Read exactly ``size`` bytes from a socket."""

    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise EOFError(f"Socket closed with {remaining} bytes left")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def make_ssl_context(*, verify_tls: bool) -> ssl.SSLContext:
    """Return an SSL context for the native IoT socket."""

    if verify_tls:
        return ssl.create_default_context()
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def extract_p2p_servers(frames: list[NativeFrame]) -> list[dict[str, Any]]:
    """Return P2P server entries from command-9 native frames."""

    for frame in frames:
        if frame.command == ControlCommand.P2P_READY_RESP and frame.payload:
            payload = frame.json()
            if isinstance(payload, list):
                return [item for item in payload if isinstance(item, dict)]
    return []
