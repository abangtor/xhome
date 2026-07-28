"""Native XHome push socket client.

The Android app wraps this transport in ``libIVIEWSPUSH.so``. Native analysis
showed that the library only provides TLS transport on port 11001 and simple
``cmd + length + payload`` framing; Java builds and parses the payloads.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Iterator
from dataclasses import dataclass
import hashlib
import json
import socket
import ssl
import struct
import time
from threading import Event
from typing import Any

from .constants import BUNDLE_ID
from .exceptions import XHomePushError

PUSH_CMD_HEARTBEAT = 0
PUSH_CMD_REGISTER = 1
PUSH_CMD_TOKEN = 2
PUSH_CMD_EVENT = 3
DEFAULT_PUSH_PORT = 11001
DEFAULT_MAX_PAYLOAD_BYTES = 1024 * 1024


@dataclass(frozen=True)
class XHomePushFrame:
    """One native push frame."""

    command: int
    payload: bytes


@dataclass(frozen=True)
class XHomePushMessage:
    """Parsed push socket message."""

    kind: str
    command: int
    payload: bytes
    token: str | None = None
    event: dict[str, Any] | None = None


class XHomePushClient:
    """Client for the native XHome push socket."""

    def __init__(
        self,
        host: str,
        *,
        user_id: int | str | None = None,
        port: int = DEFAULT_PUSH_PORT,
        timeout: float = 30,
        register_info: dict[str, Any] | str | bytes | None = None,
        verify_tls: bool = False,
        max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
        idle_reconnect_seconds: float = 130,
        force_ipv4: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.register_payload = _payload_bytes(register_info or build_push_register_info(user_id))
        self.verify_tls = verify_tls
        self.max_payload_bytes = max_payload_bytes
        self.idle_reconnect_seconds = idle_reconnect_seconds
        self.force_ipv4 = force_ipv4
        self._socket: ssl.SSLSocket | None = None

    def connect(self) -> None:
        """Connect and send the registration payload."""

        raw_socket = _create_push_connection(
            self.host,
            self.port,
            timeout=self.timeout,
            force_ipv4=self.force_ipv4,
        )
        raw_socket.settimeout(min(self.timeout, 5))
        try:
            context = _ssl_context(self.verify_tls)
            tls_socket = context.wrap_socket(raw_socket, server_hostname=self.host)
        except Exception:
            raw_socket.close()
            raise

        self._socket = tls_socket
        self.send_frame(PUSH_CMD_REGISTER, self.register_payload)

    def close(self) -> None:
        """Close the current push socket."""

        sock = self._socket
        self._socket = None
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()

    def send_frame(self, command: int, payload: dict[str, Any] | str | bytes | None = None) -> int:
        """Send one native push frame."""

        body = _payload_bytes(payload)
        frame = encode_push_frame(command, body)
        self._require_socket().sendall(frame)
        return len(frame)

    def send_heartbeat(self) -> int:
        """Send the heartbeat frame used by the Android client."""

        return self.send_frame(PUSH_CMD_HEARTBEAT, str(int(time.time() * 1000)))

    def read_frame(self) -> XHomePushFrame:
        """Read one native push frame."""

        header = self._read_exact(8)
        command, length = decode_push_header(header)
        if length < 0 or length > self.max_payload_bytes:
            raise XHomePushError(f"Invalid XHome push payload length: {length}")
        payload = self._read_exact(length) if length else b""
        return XHomePushFrame(command=command, payload=payload)

    def iter_messages(self, *, stop_event: Event | None = None) -> Iterator[XHomePushMessage]:
        """Yield parsed messages until the socket closes or ``stop_event`` is set."""

        self.connect()
        try:
            last_frame = time.monotonic()
            while stop_event is None or not stop_event.is_set():
                try:
                    frame = self.read_frame()
                except TimeoutError:
                    if time.monotonic() - last_frame >= self.idle_reconnect_seconds:
                        raise XHomePushError("XHome push socket idle timeout")
                    continue

                last_frame = time.monotonic()
                self.send_heartbeat()
                yield parse_push_frame(frame)
        finally:
            self.close()

    def _read_exact(self, length: int) -> bytes:
        """Read exactly ``length`` bytes from the TLS socket."""

        chunks: list[bytes] = []
        remaining = length
        while remaining:
            try:
                chunk = self._require_socket().recv(remaining)
            except socket.timeout as err:
                if remaining != length:
                    raise XHomePushError("Timed out while reading a partial XHome push frame") from err
                raise TimeoutError("Timed out while reading XHome push socket") from err
            if not chunk:
                raise XHomePushError("XHome push socket closed")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _require_socket(self) -> ssl.SSLSocket:
        if self._socket is None:
            raise XHomePushError("XHome push socket is not connected")
        return self._socket

    @property
    def tls_version(self) -> str | None:
        """Return the negotiated TLS version, if connected."""

        return self._socket.version() if self._socket is not None else None

    @property
    def tls_cipher(self) -> str | None:
        """Return the negotiated TLS cipher name, if connected."""

        if self._socket is None:
            return None
        cipher = self._socket.cipher()
        return cipher[0] if cipher else None


def build_push_register_info(
    user_id: int | str | None,
    *,
    model: str = "xhome-api",
    brand: str = "Python",
    bundle_id: str = BUNDLE_ID,
    imei: str | None = None,
    imsi: str | None = None,
) -> dict[str, str]:
    """Build the registration JSON used by ``SSLPushService``."""

    user = str(user_id or 0)
    return {
        "imei": imei or _stable_push_identity(user, "imei"),
        "imsi": imsi or _stable_push_identity(user, "imsi"),
        "type": model,
        "brand": brand,
        "bundle_id": bundle_id,
    }


def encode_push_frame(command: int, payload: dict[str, Any] | str | bytes | None = None) -> bytes:
    """Return a little-endian native push frame."""

    body = _payload_bytes(payload)
    return struct.pack("<ii", int(command), len(body)) + body


def decode_push_header(header: bytes) -> tuple[int, int]:
    """Decode the 8-byte little-endian push frame header."""

    if len(header) != 8:
        raise XHomePushError(f"XHome push frame header must be 8 bytes, got {len(header)}")
    return struct.unpack("<ii", header)


def parse_push_frame(frame: XHomePushFrame) -> XHomePushMessage:
    """Parse one native push frame into a higher-level message."""

    if frame.command == PUSH_CMD_TOKEN:
        return XHomePushMessage(
            kind="token",
            command=frame.command,
            payload=frame.payload,
            token=parse_push_token(frame.payload),
        )
    if frame.command == PUSH_CMD_EVENT:
        return XHomePushMessage(
            kind="event",
            command=frame.command,
            payload=frame.payload,
            event=parse_push_event(frame.payload),
        )
    return XHomePushMessage(kind="frame", command=frame.command, payload=frame.payload)


def parse_push_token(payload: bytes | str) -> str:
    """Extract the Lancens push token from a command-2 payload."""

    value = _json_payload(payload)
    token = value.get("token")
    return "" if token in (None, "") else str(token)


def parse_push_event(payload: bytes | str) -> dict[str, Any]:
    """Parse an XHome command-3 push event into an event-record-like dict."""

    event = dict(_json_payload(payload))
    aps = event.get("aps")
    if aps is None and event.get("aps2"):
        aps = _decode_base64_text(str(event["aps2"]))
    if isinstance(aps, str) and aps:
        try:
            aps = json.loads(aps)
        except json.JSONDecodeError:
            aps = {}
    if isinstance(aps, dict):
        for key in ("alert", "sound", "message", "name"):
            if key in aps and key not in event:
                event[key] = aps[key]

    if event.get("guid") and "event_guid" not in event:
        event["event_guid"] = event["guid"]
    if event.get("other") and "img" not in event:
        event["img"] = event["other"]

    if details := _decode_base64_json(event.get("func")):
        if "ts" in details and "time_stamp" not in event:
            event["time_stamp"] = details["ts"]
    if details := _decode_base64_json(event.get("info")):
        if "orientation" in details and "orientation" not in event:
            event["orientation"] = details["orientation"]

    return event


def _payload_bytes(payload: dict[str, Any] | str | bytes | None) -> bytes:
    if payload is None:
        return b""
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode("utf-8")
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _json_payload(payload: bytes | str) -> dict[str, Any]:
    text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    try:
        value = json.loads(text)
    except json.JSONDecodeError as err:
        raise XHomePushError("XHome push payload was not valid JSON") from err
    if not isinstance(value, dict):
        raise XHomePushError("XHome push payload was not a JSON object")
    return value


def _stable_push_identity(user_id: str, kind: str) -> str:
    digest = hashlib.sha1(f"xhome:{kind}:{user_id}".encode("utf-8")).hexdigest()[:12]
    return f"python_{digest}_{user_id}"


def _ssl_context(verify_tls: bool) -> ssl.SSLContext:
    if verify_tls:
        context = ssl.create_default_context()
    else:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    # libIVIEWSPUSH.so behaves like an OpenSSL/TLS-1.2-era client. Some HA
    # OpenSSL builds otherwise trigger a server-side handshake_failure alert.
    try:
        context.maximum_version = ssl.TLSVersion.TLSv1_2
    except (AttributeError, ValueError):
        pass
    try:
        context.set_ciphers("DEFAULT:@SECLEVEL=0")
    except ssl.SSLError:
        pass
    return context


def _create_push_connection(
    host: str,
    port: int,
    *,
    timeout: float,
    force_ipv4: bool,
) -> socket.socket:
    """Open the native push TCP socket."""

    if not force_ipv4:
        return socket.create_connection((host, port), timeout=timeout)

    errors: list[OSError] = []
    for family, socktype, proto, _canonname, sockaddr in socket.getaddrinfo(
        host,
        port,
        family=socket.AF_INET,
        type=socket.SOCK_STREAM,
    ):
        sock = socket.socket(family, socktype, proto)
        try:
            sock.settimeout(timeout)
            sock.connect(sockaddr)
            return sock
        except OSError as err:
            errors.append(err)
            sock.close()
    if errors:
        raise errors[-1]
    raise OSError(f"No IPv4 address found for XHome push host {host!r}")


def _decode_base64_text(value: str) -> str:
    try:
        padded = value + "=" * (-len(value) % 4)
        return base64.b64decode(padded, validate=False).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return ""


def _decode_base64_json(value: Any) -> dict[str, Any] | None:
    if value in (None, ""):
        return None
    try:
        decoded = _decode_base64_text(str(value))
        parsed = json.loads(decoded)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
