"""Receive-only XHome live-stream sidecar runner."""

from __future__ import annotations

import argparse
import json
import shlex
import struct
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from .client import unwrap_response
from .constants import normalize_region
from .live import (
    MEDIA_HEADER_BYTES,
    ControlCommand,
    LiveCallback,
    LiveSessionMetadata,
    callback_to_media_frame,
    live_session_from_token_payload,
)
from .live_transport import XHomeLiveCloudTransport, extract_p2p_servers
from .live_p2p import XHomeP2PProbe

RECORD_MAGIC = b"XHF1"
RECORD_HEADER = struct.Struct("<4siiiI")


@dataclass
class RelayStats:
    """Runtime counters for one live relay."""

    callbacks: int = 0
    media_frames: int = 0
    h264_frames: int = 0
    g711_frames: int = 0
    jpeg_frames: int = 0
    unknown_frames: int = 0
    started: bool = False

    def as_dict(self) -> dict[str, int | bool]:
        return {
            "callbacks": self.callbacks,
            "media_frames": self.media_frames,
            "h264_frames": self.h264_frames,
            "g711_frames": self.g711_frames,
            "jpeg_frames": self.jpeg_frames,
            "unknown_frames": self.unknown_frames,
            "started": self.started,
        }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"xhome-live-sidecar: {exc}", file=sys.stderr)
        return 1
    if result is not None:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="XHome live-stream sidecar utilities")
    subparsers = parser.add_subparsers(required=True)

    relay = subparsers.add_parser("relay", help="Relay native-helper callbacks to raw media files")
    relay.add_argument("--uid", required=True)
    relay.add_argument("--token", required=True)
    relay.add_argument("--native-iot-host", required=True)
    relay.add_argument("--device-id", type=int)
    relay.add_argument("--model")
    relay.add_argument("--bridge-command", required=True, help="Native helper command; split with shell-like quoting")
    relay.add_argument("--h264-out", type=Path, help="Write raw H.264 payloads here")
    relay.add_argument("--g711-out", type=Path, help="Write raw G.711 audio payloads here")
    relay.add_argument("--jpeg-dir", type=Path, help="Write JPEG frames into this directory")
    relay.add_argument("--duration", type=float, help="Stop after this many seconds")
    relay.add_argument("--no-auto-start", action="store_true", help="Do not send command 20 when P2P becomes ready")
    relay.add_argument("--stats-interval", type=float, default=10.0)
    relay.set_defaults(func=cmd_relay)

    strip = subparsers.add_parser("strip-callbacks", help="Strip a saved helper callback stream into raw media files")
    strip.add_argument("input", type=Path)
    strip.add_argument("--h264-out", type=Path)
    strip.add_argument("--g711-out", type=Path)
    strip.add_argument("--jpeg-dir", type=Path)
    strip.set_defaults(func=cmd_strip_callbacks)

    explain = subparsers.add_parser("helper-contract", help="Print the native-helper stdio protocol")
    explain.set_defaults(func=cmd_helper_contract)

    probe = subparsers.add_parser("cloud-probe", help="Probe the portable native-IoT TLS login phase")
    probe.add_argument("--uid", required=True)
    probe.add_argument("--token", required=True)
    probe.add_argument("--native-iot-host", required=True)
    probe.add_argument("--duration", type=float, default=5.0)
    probe.add_argument("--timeout", type=float, default=10.0)
    probe.add_argument("--send-start", action="store_true", help="Send command 20 after login, then command 21 before exit")
    probe.add_argument("--p2p-probe", action="store_true", help="Probe the first returned UDP relay after command 9")
    probe.add_argument(
        "--insecure-skip-verify",
        action="store_true",
        help="Disable TLS certificate verification; native hosts may present mismatched certificates",
    )
    probe.set_defaults(func=cmd_cloud_probe)

    return parser


def cmd_relay(args: argparse.Namespace) -> dict[str, Any]:
    metadata = LiveSessionMetadata(
        uid=args.uid,
        token=args.token,
        native_iot_host=args.native_iot_host,
        device_id=args.device_id,
        model=args.model,
    )
    bridge_argv = shlex.split(args.bridge_command)
    if not bridge_argv:
        raise ValueError("Bridge command is empty")

    process = subprocess.Popen(  # noqa: S603
        bridge_argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=None,
        bufsize=0,
    )
    if process.stdin is None or process.stdout is None:
        raise RuntimeError("Failed to open native helper pipes")

    try:
        write_helper_json(process.stdin, {"action": "session", **metadata.as_bridge_payload()})
        stats = relay_callbacks(
            callbacks=iter_callback_records(process.stdout),
            command_sink=process.stdin,
            header_bytes=metadata.media_header_bytes,
            h264_out=args.h264_out,
            g711_out=args.g711_out,
            jpeg_dir=args.jpeg_dir,
            duration=args.duration,
            auto_start=not args.no_auto_start,
            stats_interval=args.stats_interval,
            start_command=metadata.start_command,
        )
    finally:
        if process.stdin:
            try:
                write_helper_json(process.stdin, {"action": "stop", "cmd": metadata.stop_command})
            except OSError:
                pass
            process.stdin.close()
        try:
            process.terminate()
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    return stats.as_dict()


def cmd_strip_callbacks(args: argparse.Namespace) -> dict[str, Any]:
    with args.input.open("rb") as stream:
        stats = relay_callbacks(
            callbacks=iter_callback_records(stream),
            command_sink=None,
            h264_out=args.h264_out,
            g711_out=args.g711_out,
            jpeg_dir=args.jpeg_dir,
            auto_start=False,
        )
    return stats.as_dict()


def cmd_helper_contract(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "stdin": [
            {"action": "session", "uid": "...", "token": "...", "native_iot_host": "usaiotd.lancens.com"},
            {"action": "send", "cmd": 20, "data_base64": ""},
            {"action": "stop", "cmd": 21},
        ],
        "stdout_record": {
            "header": "magic XHF1 + int32 callback_type + int32 command + int32 status + uint32 payload_len",
            "payload": "Raw bytes from IVIEWSAVAPIs.AVAPISCallback",
        },
        "media": {
            "header_bytes": MEDIA_HEADER_BYTES,
            "h264_types": [160, 161, 162],
            "g711_type": 164,
            "jpeg_type": 165,
        },
    }


def cmd_cloud_probe(args: argparse.Namespace) -> dict[str, Any]:
    metadata = LiveSessionMetadata(
        uid=args.uid,
        token=args.token,
        native_iot_host=args.native_iot_host,
    )
    with XHomeLiveCloudTransport(
        metadata,
        timeout=args.timeout,
        verify_tls=not args.insecure_skip_verify,
    ) as transport:
        transport.login()
        frames = []
        if args.send_start:
            frames.extend(transport.read_available(duration=min(2.0, args.duration)))
            transport.send_frame(metadata.start_command)
        frames.extend(transport.read_available(duration=args.duration))
        if args.send_start:
            transport.send_frame(metadata.stop_command)

    p2p_probe = None
    p2p_servers = extract_p2p_servers(frames)
    if args.p2p_probe and p2p_servers:
        first = p2p_servers[0]
        p2p_probe = XHomeP2PProbe(
            uid=args.uid,
            relay_host=str(first["IP"]),
            relay_port=int(first["Port"]),
        ).run()

    return {
        "frames": [
            {
                "command": frame.command,
                "payload_length": len(frame.payload),
                "payload_text": frame.text if len(frame.payload) <= 4096 else frame.text[:4096],
            }
            for frame in frames
        ],
        "p2p_servers": p2p_servers,
        "p2p_probe": p2p_probe,
    }


def relay_callbacks(
    *,
    callbacks: Iterator[LiveCallback],
    command_sink: BinaryIO | None,
    header_bytes: int = MEDIA_HEADER_BYTES,
    h264_out: Path | None = None,
    g711_out: Path | None = None,
    jpeg_dir: Path | None = None,
    duration: float | None = None,
    auto_start: bool = True,
    stats_interval: float = 10.0,
    start_command: int = int(ControlCommand.AV_START_REQ),
) -> RelayStats:
    """Relay native callbacks to raw payload files."""

    stats = RelayStats()
    started_at = time.monotonic()
    next_stats_at = started_at + stats_interval if stats_interval > 0 else float("inf")

    with output_file(h264_out) as h264_stream, output_file(g711_out) as g711_stream:
        if jpeg_dir:
            jpeg_dir.mkdir(parents=True, exist_ok=True)

        for callback in callbacks:
            now = time.monotonic()
            if duration is not None and now - started_at >= duration:
                break

            stats.callbacks += 1
            if auto_start and command_sink is not None and callback.is_ready and not stats.started:
                write_helper_json(command_sink, {"action": "send", "cmd": start_command, "data_base64": ""})
                stats.started = True

            frame = callback_to_media_frame(callback, header_bytes=header_bytes)
            if frame is None:
                continue

            stats.media_frames += 1
            if frame.is_h264:
                stats.h264_frames += 1
                if h264_stream:
                    h264_stream.write(frame.payload)
                    h264_stream.flush()
            elif frame.is_g711:
                stats.g711_frames += 1
                if g711_stream:
                    g711_stream.write(frame.payload)
                    g711_stream.flush()
            elif frame.is_jpeg:
                stats.jpeg_frames += 1
                if jpeg_dir:
                    (jpeg_dir / f"frame-{stats.jpeg_frames:06d}.jpg").write_bytes(frame.payload)
            else:
                stats.unknown_frames += 1

            if now >= next_stats_at:
                print(json.dumps({"event": "stats", **stats.as_dict()}), file=sys.stderr, flush=True)
                next_stats_at = now + stats_interval

    return stats


def iter_callback_records(stream: BinaryIO) -> Iterator[LiveCallback]:
    """Yield callbacks from the native-helper binary stdout protocol."""

    while True:
        header = read_exact(stream, RECORD_HEADER.size)
        if not header:
            return
        magic, callback_type, command, status, payload_len = RECORD_HEADER.unpack(header)
        if magic != RECORD_MAGIC:
            raise ValueError(f"Invalid callback record magic: {magic!r}")
        payload = read_exact(stream, payload_len)
        if payload is None:
            raise EOFError(f"Unexpected EOF while reading {payload_len} callback payload bytes")
        yield LiveCallback(callback_type=callback_type, command=command, status=status, payload=payload)


def read_exact(stream: BinaryIO, size: int) -> bytes | None:
    """Read exactly ``size`` bytes, returning ``None`` on clean EOF before data."""

    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            if not chunks:
                return None
            raise EOFError(f"Unexpected EOF with {remaining} bytes left")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def write_helper_json(stream: BinaryIO, payload: dict[str, Any]) -> None:
    stream.write(json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n")
    stream.flush()


def encode_callback_record(callback: LiveCallback) -> bytes:
    """Encode one callback record, mostly for tests and native-helper authors."""

    return RECORD_HEADER.pack(
        RECORD_MAGIC,
        int(callback.callback_type),
        int(callback.command),
        int(callback.status),
        len(callback.payload),
    ) + callback.payload


class output_file:
    """Tiny optional binary file context manager."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.stream: BinaryIO | None = None

    def __enter__(self) -> BinaryIO | None:
        if self.path is None:
            return None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("ab")
        return self.stream

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self.stream:
            self.stream.close()


def metadata_from_client_token(
    *,
    client: Any,
    uid: str,
    region: str | int,
    device_id: int | None = None,
    model: str | int | None = None,
) -> LiveSessionMetadata:
    """Fetch REST live-token metadata and normalize it for the sidecar."""

    token_payload = unwrap_response(client.get_device_token(device_id=device_id, uid=None) if device_id else client.get_device_token(uid=uid))
    if not isinstance(token_payload, dict):
        raise ValueError("Live-token endpoint returned an unexpected payload")
    return live_session_from_token_payload(
        uid=uid,
        native_iot_host=normalize_region(region).native_iot_host,
        payload=token_payload,
        device_id=device_id,
        model=model,
    )


if __name__ == "__main__":
    raise SystemExit(main())
