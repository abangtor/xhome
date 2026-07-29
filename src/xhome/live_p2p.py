"""Portable UDP/P2P pieces for XHome live streaming."""

from __future__ import annotations

import json
import secrets
import socket
import struct
import time
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Any, BinaryIO, Callable

from .live import LiveAppMediaAssembler, LiveAppMediaFrame, MediaType, parse_live_app_media_packet
from .live_transport import decode_native_frame_header, encode_native_frame

UDP_HEADER = struct.Struct("<HHH")
CLIENT_CONTROL_CHANNEL = 1
MEDIA_CHANNEL = 2
RAW_CHANNEL = 4


class P2PPacketType(IntEnum):
    """Packet type values recovered from ``libIVIEWSAVAPIs.so``."""

    RELAY_TOUCH = 0
    CLIENT_CONNECT = 6
    CLIENT_CONNECT_RESPONSE = 7
    DIRECT_PUNCH = 11
    DIRECT_PUNCH_RESPONSE = 12
    KCP_DATA = 13
    HEARTBEAT = 14
    RELAY_INFO = 15
    RELAY_CONNECTED = 16
    DIRECT_KCP_DATA = 18
    RELAY_KCP_DATA = 19


class P2PAddressKind(IntEnum):
    """Native candidate address categories from the client type-7 response."""

    LOCAL = 0
    PUBLIC = 1
    RELAY = 2


@dataclass(frozen=True)
class UdpPacket:
    """One XHome P2P UDP packet."""

    packet_type: int
    channel: int
    payload: bytes

    @property
    def text(self) -> str:
        return self.payload.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.text)


@dataclass(frozen=True)
class P2PAddress:
    """One peer/relay candidate returned by the P2P relay."""

    host: str
    port: int
    kind: P2PAddressKind

    @property
    def address(self) -> tuple[str, int]:
        return self.host, self.port

    def as_dict(self) -> dict[str, Any]:
        return {"host": self.host, "port": self.port, "kind": self.kind.name.lower()}


@dataclass(frozen=True)
class ClientConnectResponse:
    """Parsed relay type-7 response."""

    uid: str
    public_ip: str | None
    public_port: int | None
    online: str | None
    nat_type: str | None
    candidates: tuple[P2PAddress, ...]
    raw: dict[str, Any]

    @classmethod
    def from_payload(cls, payload: bytes) -> ClientConnectResponse:
        data = json.loads(payload.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Expected client-connect response object")
        candidates: list[P2PAddress] = []
        peer_local_port = int_or_none(data.get("PeerLocalPort"))
        if peer_local_port is not None:
            for item in data.get("PeerLocalIP") or []:
                host = item.get("IP") if isinstance(item, dict) else None
                if host:
                    candidates.append(P2PAddress(str(host), peer_local_port, P2PAddressKind.LOCAL))
        peer_public_ip = data.get("PeerPublicIP")
        peer_public_port = int_or_none(data.get("PeerPublicPort"))
        if peer_public_ip and peer_public_port is not None:
            candidates.append(P2PAddress(str(peer_public_ip), peer_public_port, P2PAddressKind.PUBLIC))
        for item in data.get("RelayAddress") or []:
            if not isinstance(item, dict):
                continue
            host = item.get("IP")
            port = int_or_none(item.get("Port"))
            if host and port is not None:
                candidates.append(P2PAddress(str(host), port, P2PAddressKind.RELAY))
        public_ip = data.get("PublicIp") if "PublicIp" in data else data.get("PublicIP")
        return cls(
            uid=str(data.get("Uid") or data.get("UID") or ""),
            public_ip=str(public_ip) if public_ip is not None else None,
            public_port=int_or_none(data.get("PublicPort")),
            online=str(data.get("Online")) if data.get("Online") is not None else None,
            nat_type=str(data.get("NatType")) if data.get("NatType") is not None else None,
            candidates=tuple(candidates),
            raw=data,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "public_ip": self.public_ip,
            "public_port": self.public_port,
            "online": self.online,
            "nat_type": self.nat_type,
            "candidates": [candidate.as_dict() for candidate in self.candidates],
            "raw": self.raw,
        }


def encode_udp_packet(packet_type: int, payload: bytes = b"", *, channel: int = 0) -> bytes:
    """Encode the six-byte XHome P2P UDP header plus payload."""

    return UDP_HEADER.pack(packet_type, channel, 0) + payload


def decode_udp_packet(data: bytes) -> UdpPacket:
    """Decode an XHome P2P UDP packet."""

    if len(data) < UDP_HEADER.size:
        raise ValueError(f"UDP packet is too short: {len(data)}")
    packet_type, channel, _reserved = UDP_HEADER.unpack(data[: UDP_HEADER.size])
    return UdpPacket(packet_type=packet_type, channel=channel, payload=data[UDP_HEADER.size :])


def build_json_payload(payload: dict[str, Any]) -> bytes:
    """Encode compact JSON for P2P control packets."""

    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def build_client_connect_payload(*, uid: str, local_ip: str, local_port: int, key: str | None = None) -> bytes:
    """Build the client-connecting JSON payload used by packet type 6."""

    return build_client_connect_payload_for_ips(uid=uid, local_ips=[local_ip], local_port=local_port, key=key)


def build_client_connect_payload_for_ips(
    *,
    uid: str,
    local_ips: list[str],
    local_port: int,
    key: str | None = None,
) -> bytes:
    """Build the client-connecting JSON payload used by packet type 6."""

    port = str(local_port)
    return build_json_payload(
        {
            "LocalIp": [{"IP": local_ip} for local_ip in local_ips],
            "Uid": uid,
            "Port": port,
            "Key": key if key is not None else port,
        }
    )


def build_peer_punch_payload(*, uid: str, address_kind: P2PAddressKind | int | str) -> bytes:
    """Build the client punching payload used by packet type 11."""

    kind = P2PAddressKind(address_kind)
    return build_json_payload({"Uid": uid, "Key": "", "Type": str(int(kind))})


def build_peer_punch_response_payload(*, uid: str, port_token: str) -> bytes:
    """Build the response payload used by packet type 12."""

    return build_json_payload({"Time": current_millis(), "Uid": uid, "Key": "", "Port": port_token})


def build_uid_payload(*, uid: str, include_key: bool = False) -> bytes:
    """Build simple UID-bearing payloads used by heartbeat/relay-info packets."""

    payload = {"Uid": uid}
    if include_key:
        payload["Key"] = ""
    return build_json_payload(payload)


def build_relay_touch_nonce(*, now: float | None = None, tick: int | None = None) -> bytes:
    """Build the native-looking eight-byte relay touch nonce.

    PCAPs from the Android app show four-byte little-endian Unix seconds
    followed by four opaque bytes. The relay echoes the opaque nonce back; no
    payload cryptography has been observed here.
    """

    seconds = int(time.time() if now is None else now)
    native_tick = int(time.monotonic() * 45_000) if tick is None else tick
    return seconds.to_bytes(4, "little", signed=False) + (native_tick & 0xFFFFFFFF).to_bytes(
        4, "little", signed=False
    )


def build_direct_touch_payload(*, nonce: bytes | None = None, now: float | None = None) -> bytes:
    """Build the eight-byte direct-LAN channel-4 touch payload."""

    nonce = nonce or build_relay_touch_nonce(now=now)
    if len(nonce) != 8:
        raise ValueError("Direct touch nonce must be exactly 8 bytes")
    return nonce


def build_relay_touch_payload(*, uid: str, nonce: bytes | None = None, now: float | None = None) -> bytes:
    """Build the short channel-4 relay touch payload seen in the Android app.

    The native client sends packet type 18 on UDP envelope channel 4 with eight
    opaque bytes followed by the target UID. The relay echoes the opaque bytes
    back on packet type 19/channel 4 and sends a type-16 relay-connected notice.
    The bytes do not appear to be KCP; they are treated as an opaque nonce here.
    """

    nonce = nonce or build_relay_touch_nonce(now=now)
    if len(nonce) != 8:
        raise ValueError("Relay touch nonce must be exactly 8 bytes")
    return nonce + uid.encode("ascii")


def encode_kcp_udp_packet(
    packet_type: int,
    kcp_payload: bytes,
    *,
    channel: int,
    uid_suffix: str | None = None,
) -> bytes:
    """Wrap one KCP segment in the native UDP envelope.

    Direct relay mode appends the 20-byte UID after the KCP payload before
    sending packet type 18/19.
    """

    payload = kcp_payload + (uid_suffix.encode("ascii") if uid_suffix else b"")
    return encode_udp_packet(packet_type, payload, channel=channel)


def parse_client_connect_responses(packets: list[UdpPacket]) -> list[ClientConnectResponse]:
    """Parse all type-7 packets in a packet list."""

    responses: list[ClientConnectResponse] = []
    for packet in packets:
        if packet.packet_type == P2PPacketType.CLIENT_CONNECT_RESPONSE and packet.payload:
            responses.append(ClientConnectResponse.from_payload(packet.payload))
    return responses


class XHomeP2PProbe:
    """Small UDP relay probe for the native P2P rendezvous phase."""

    def __init__(self, *, uid: str, relay_host: str, relay_port: int, timeout: float = 0.5) -> None:
        self.uid = uid
        self.relay = (relay_host, relay_port)
        self.timeout = timeout

    def run(self, *, attempts: int = 10, interval: float = 0.05) -> dict[str, Any]:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(("0.0.0.0", 0))
            sock.settimeout(self.timeout)
            local_ip = best_effort_local_ip()
            local_ips = [local_ip]
            local_port = sock.getsockname()[1]
            sock.sendto(encode_udp_packet(0), self.relay)
            payload = build_client_connect_payload(uid=self.uid, local_ip=local_ip, local_port=local_port)
            packets: list[UdpPacket] = []

            for _ in range(attempts):
                sock.sendto(encode_udp_packet(P2PPacketType.CLIENT_CONNECT, payload), self.relay)
                packets.extend(read_udp_available(sock, timeout=self.timeout))
                time.sleep(interval)

            responses = parse_client_connect_responses(packets)
            return {
                "local_ip": local_ip,
                "local_ips": local_ips,
                "local_port": local_port,
                "relay": {"host": self.relay[0], "port": self.relay[1]},
                "client_connect_responses": [response.as_dict() for response in responses],
                "packets": [
                    {
                        "type": packet.packet_type,
                        "channel": packet.channel,
                        "payload_length": len(packet.payload),
                        "payload_text": packet.text if len(packet.payload) <= 4096 else packet.text[:4096],
                    }
                    for packet in packets
                ],
            }
        finally:
            sock.close()


class XHomeP2PRendezvousProbe:
    """Best-effort client-side P2P rendezvous probe.

    This mirrors the native client state machine through the point where it can
    discover peer candidates, send direct punching packets, and answer peer
    handshakes. KCP media/control is deliberately kept as the next layer.
    """

    def __init__(
        self,
        *,
        uid: str,
        relays: list[tuple[str, int]],
        timeout: float = 0.2,
        local_ips: list[str] | None = None,
        direct_punch_enabled: bool = True,
    ) -> None:
        if not relays:
            raise ValueError("At least one relay is required")
        self.uid = uid
        self.relays = relays
        self.timeout = timeout
        self.local_ips = local_ips
        self.direct_punch_enabled = direct_punch_enabled

    def run(
        self,
        *,
        duration: float = 8.0,
        interval: float = 0.05,
        kcp_start_command: int | None = None,
        kcp_start_interval: float = 0.5,
        direct_touch_burst_size: int = 4,
        relay_touch_burst_size: int = 4,
        relay_touch_interval: float = 2.0,
        relay_touch_time_offset: float = 0.0,
        heartbeat_interval: float = 2.0,
        h264_out: Path | None = None,
        g711_out: Path | None = None,
        jpeg_dir: Path | None = None,
        on_frame: Callable[[LiveAppMediaFrame], None] | None = None,
        on_stats: Callable[[dict[str, Any]], None] | None = None,
        on_ready: Callable[[], None] | None = None,
        stop_event: Any | None = None,
    ) -> dict[str, Any]:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(("0.0.0.0", 0))
            sock.settimeout(self.timeout)
            local_ip = best_effort_local_ip()
            local_ips = self.local_ips or best_effort_local_ips()
            local_port = sock.getsockname()[1]
            port_token = str(local_port)
            connect_payload = build_client_connect_payload_for_ips(
                uid=self.uid,
                local_ips=local_ips,
                local_port=local_port,
            )
            relay_info_payload = build_uid_payload(uid=self.uid, include_key=True)
            heartbeat_payload = build_uid_payload(uid=self.uid)
            packets: list[UdpPacket] = []
            responses: list[ClientConnectResponse] = []
            response_keys: set[str] = set()
            candidates: dict[tuple[str, int, P2PAddressKind], P2PAddress] = {}
            selected_peer: tuple[str, int] | None = None
            ready_notified = False
            loop_ticks = 0
            sent_counts: dict[str, int] = {
                "relay_touch": 0,
                "client_connect": 0,
                "direct_punch": 0,
                "direct_touch_channel4": 0,
                "punch_response": 0,
                "relay_info": 0,
                "relay_touch_channel4": 0,
                "heartbeat": 0,
                "kcp_start": 0,
            }

            def probe_stats() -> dict[str, Any]:
                stats = media_probe.as_dict()
                stats.update(
                    {
                        "candidate_count": len(candidates),
                        "local_port": local_port,
                        "loop_ticks": loop_ticks,
                        "packets": len(packets),
                        "selected_peer": (
                            {"host": selected_peer[0], "port": selected_peer[1]} if selected_peer else None
                        ),
                        "sent": dict(sent_counts),
                    }
                )
                return stats

            def emit_probe_stats(stats: dict[str, Any]) -> None:
                if on_stats is not None:
                    merged = probe_stats()
                    merged.update(stats)
                    on_stats(merged)

            kcp_probe = KcpStartProbe(uid=self.uid, sock=sock, start_command=kcp_start_command)
            media_probe = KcpMediaProbe(
                uid=self.uid,
                sock=sock,
                h264_out=h264_out,
                g711_out=g711_out,
                jpeg_dir=jpeg_dir,
                on_frame=on_frame,
                on_stats=emit_probe_stats,
            )
            next_kcp_start = 0.0
            next_discovery = 0.0
            next_direct_touch = 0.0
            next_relay_touch = 0.0
            next_heartbeat = 0.0
            next_stats = 0.0

            for relay in self.relays:
                sock.sendto(encode_udp_packet(P2PPacketType.RELAY_TOUCH), relay)
                sent_counts["relay_touch"] += 1

            try:
                deadline = time.monotonic() + duration
                while time.monotonic() < deadline and (stop_event is None or not stop_event.is_set()):
                    loop_ticks += 1
                    now = time.monotonic()
                    if selected_peer is None and now >= next_discovery:
                        for relay in self.relays:
                            sock.sendto(encode_udp_packet(P2PPacketType.CLIENT_CONNECT, connect_payload), relay)
                            sent_counts["client_connect"] += 1

                        for candidate in list(candidates.values()):
                            if self.direct_punch_enabled and candidate.kind != P2PAddressKind.RELAY:
                                punch_payload = build_peer_punch_payload(uid=self.uid, address_kind=candidate.kind)
                                sock.sendto(
                                    encode_udp_packet(P2PPacketType.DIRECT_PUNCH, punch_payload),
                                    candidate.address,
                                )
                                sent_counts["direct_punch"] += 1
                        next_discovery = now + interval

                    relay_candidates = [
                        candidate for candidate in candidates.values() if candidate.kind == P2PAddressKind.RELAY
                    ]
                    relay_addresses = {candidate.address for candidate in relay_candidates}
                    direct_touch_targets = {
                        candidate.address for candidate in candidates.values() if candidate.kind != P2PAddressKind.RELAY
                    }
                    if selected_peer is not None and selected_peer not in relay_addresses:
                        direct_touch_targets.add(selected_peer)
                    if direct_touch_targets and now >= next_direct_touch:
                        for target in direct_touch_targets:
                            for _ in range(direct_touch_burst_size):
                                sock.sendto(
                                    encode_udp_packet(
                                        P2PPacketType.KCP_DATA,
                                        build_direct_touch_payload(now=time.time() + relay_touch_time_offset),
                                        channel=RAW_CHANNEL,
                                    ),
                                    target,
                                )
                                sent_counts["direct_touch_channel4"] += 1
                        next_direct_touch = now + relay_touch_interval

                    if relay_candidates and now >= next_relay_touch:
                        for candidate in relay_candidates:
                            sock.sendto(
                                encode_udp_packet(P2PPacketType.RELAY_INFO, relay_info_payload),
                                candidate.address,
                            )
                            sent_counts["relay_info"] += 1
                            for _ in range(relay_touch_burst_size):
                                sock.sendto(
                                    encode_udp_packet(
                                        P2PPacketType.DIRECT_KCP_DATA,
                                        build_relay_touch_payload(
                                            uid=self.uid,
                                            now=time.time() + relay_touch_time_offset,
                                        ),
                                        channel=RAW_CHANNEL,
                                    ),
                                    candidate.address,
                                )
                                sent_counts["relay_touch_channel4"] += 1
                        next_relay_touch = now + relay_touch_interval

                    if kcp_start_command is not None and now >= next_kcp_start:
                        sent_counts["kcp_start"] += kcp_probe.send_start_packets(candidates.values())
                        next_kcp_start = now + kcp_start_interval

                    if now >= next_heartbeat:
                        heartbeat_targets = set(self.relays)
                        heartbeat_targets.update(candidate.address for candidate in relay_candidates)
                        if selected_peer is not None:
                            heartbeat_targets.add(selected_peer)
                        for target in heartbeat_targets:
                            sock.sendto(encode_udp_packet(P2PPacketType.HEARTBEAT, heartbeat_payload), target)
                            sent_counts["heartbeat"] += 1
                        next_heartbeat = now + heartbeat_interval

                    for received, addr in read_udp_available_with_addresses(
                        sock,
                        timeout=self.timeout,
                        max_duration=interval,
                        max_packets=64,
                    ):
                        packets.append(received)
                        media_probe.receive_packet(received, addr)
                        kcp_probe.receive_packet(received, addr)
                        if received.packet_type == P2PPacketType.CLIENT_CONNECT_RESPONSE and received.payload:
                            response = ClientConnectResponse.from_payload(received.payload)
                            response_key = json.dumps(response.raw, sort_keys=True)
                            if response_key not in response_keys:
                                response_keys.add(response_key)
                                responses.append(response)
                            for candidate in response.candidates:
                                candidates[(candidate.host, candidate.port, candidate.kind)] = candidate
                        elif received.packet_type == P2PPacketType.DIRECT_PUNCH and received.payload:
                            response_payload = build_peer_punch_response_payload(uid=self.uid, port_token=port_token)
                            sock.sendto(encode_udp_packet(P2PPacketType.DIRECT_PUNCH_RESPONSE, response_payload), addr)
                            sent_counts["punch_response"] += 1
                        elif received.packet_type in {
                            P2PPacketType.DIRECT_PUNCH_RESPONSE,
                            P2PPacketType.HEARTBEAT,
                            P2PPacketType.RELAY_CONNECTED,
                            P2PPacketType.KCP_DATA,
                            P2PPacketType.DIRECT_KCP_DATA,
                            P2PPacketType.RELAY_KCP_DATA,
                        }:
                            selected_peer = addr
                            if on_ready is not None and not ready_notified:
                                on_ready()
                                ready_notified = True
                    media_probe.update()
                    if on_stats is not None and now >= next_stats:
                        on_stats(probe_stats())
                        next_stats = now + 2.0
                    time.sleep(interval)

                return {
                    "local_ip": local_ip,
                    "local_ips": local_ips,
                    "local_port": local_port,
                    "direct_punch_enabled": self.direct_punch_enabled,
                    "relays": [{"host": host, "port": port} for host, port in self.relays],
                    "sent": sent_counts,
                    "selected_peer": {"host": selected_peer[0], "port": selected_peer[1]} if selected_peer else None,
                    "candidates": [candidate.as_dict() for candidate in candidates.values()],
                    "client_connect_responses": [response.as_dict() for response in responses],
                    "media_probe": probe_stats(),
                    "kcp_start_probe": kcp_probe.as_dict(),
                    "packet_type_counts": packet_type_counts(packets),
                    "packets": [packet_summary(packet) for packet in packets[:10]],
                    "packets_truncated": max(0, len(packets) - 10),
                }
            finally:
                media_probe.close()
        finally:
            sock.close()


class KcpStartProbe:
    """Active probe that sends the native AV-start command over KCP.

    Native ``IVIEWSClient::send`` wraps command bytes as the same 8-byte native
    command frame used by the TLS control socket. ``P2P_manger::write`` then
    sends ordinary device commands through KCP channel 2. This class tries that
    shape against all discovered candidate paths.
    """

    def __init__(self, *, uid: str, sock: socket.socket, start_command: int | None) -> None:
        self.uid = uid
        self.sock = sock
        self.start_command = start_command
        self.error: str | None = None
        self.paths: dict[tuple[str, int, int, str], Any] = {}
        self.decoded_payloads: list[dict[str, Any]] = []

    def send_start_packets(self, candidates: Any) -> int:
        """Send one start frame through each initialized KCP path."""

        if self.start_command is None:
            return 0
        if self.error:
            return 0
        sent = 0
        payload = encode_native_frame(self.start_command)
        for candidate in candidates:
            try:
                channels = self._channels_for_candidate(candidate)
            except Exception as exc:  # noqa: BLE001
                self.error = str(exc)
                return sent
            for channel in channels:
                try:
                    channel.send_media(payload)
                    channel.update()
                except Exception as exc:  # noqa: BLE001
                    self.error = str(exc)
                    return sent
                sent += 1
        return sent

    def receive_packet(self, packet: UdpPacket, addr: tuple[str, int]) -> None:
        """Feed inbound KCP packets to matching candidate paths."""

        if self.error or self.start_command is None:
            return
        for channel in self.paths.values():
            for kcp_channel, payload in channel.receive_packet(packet):
                self.decoded_payloads.append(
                    {
                        "source": {"host": addr[0], "port": addr[1]},
                        "kcp_channel": kcp_channel,
                        **native_payload_summary(payload),
                    }
                )

    def as_dict(self) -> dict[str, Any] | None:
        if self.start_command is None:
            return None
        return {
            "start_command": self.start_command,
            "error": self.error,
            "paths": [
                {"host": host, "port": port, "packet_type": packet_type, "mode": mode}
                for host, port, packet_type, mode in self.paths
            ],
            "decoded_payloads": self.decoded_payloads[:20],
            "decoded_payloads_truncated": max(0, len(self.decoded_payloads) - 20),
        }

    def _channels_for_candidate(self, candidate: P2PAddress) -> list[Any]:
        paths: list[Any] = []
        if candidate.kind == P2PAddressKind.RELAY:
            for packet_type in (P2PPacketType.DIRECT_KCP_DATA, P2PPacketType.RELAY_KCP_DATA):
                paths.append(self._channel(candidate, int(packet_type), "relay"))
            return paths
        return [self._channel(candidate, int(P2PPacketType.KCP_DATA), "direct")]

    def _channel(self, candidate: P2PAddress, packet_type: int, mode: str) -> Any:
        key = (candidate.host, candidate.port, packet_type, mode)
        if key not in self.paths:
            from .live_kcp import XHomeKcpChannels

            def send_udp(data: bytes, address: tuple[str, int] = candidate.address) -> None:
                self.sock.sendto(data, address)

            try:
                self.paths[key] = XHomeKcpChannels(
                    uid=self.uid,
                    send_udp=send_udp,
                    relay_tunnel=mode == "relay",
                    outbound_packet_type=packet_type,
                    uid_suffix=self.uid if mode == "relay" else None,
                )
            except Exception as exc:  # noqa: BLE001
                self.error = str(exc)
                raise
        return self.paths[key]


class KcpMediaProbe:
    """Passive receiver for media KCP packets from the relay.

    In the Android capture, media starts as inbound packet type 19/channel 2
    without a preceding client-side KCP application payload. Creating the KCP
    channel on first inbound media lets the KCP library emit ACKs and gives us
    completed app-media payloads to assemble.
    """

    def __init__(
        self,
        *,
        uid: str,
        sock: socket.socket,
        h264_out: Path | None = None,
        g711_out: Path | None = None,
        jpeg_dir: Path | None = None,
        on_frame: Callable[[LiveAppMediaFrame], None] | None = None,
        on_stats: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.uid = uid
        self.sock = sock
        self.jpeg_dir = jpeg_dir
        self.on_frame = on_frame
        self.on_stats = on_stats
        self.h264_stream = open_output(h264_out)
        self.g711_stream = open_output(g711_out)
        if self.jpeg_dir:
            self.jpeg_dir.mkdir(parents=True, exist_ok=True)
        self.error: str | None = None
        self.paths: dict[tuple[str, int, int, str], Any] = {}
        self.assembler = LiveAppMediaAssembler()
        self.udp_packets = 0
        self.kcp_payloads = 0
        self.app_packets = 0
        self.frames = 0
        self.h264_frames = 0
        self.g711_frames = 0
        self.jpeg_frames = 0
        self.last_packet_at: int | None = None
        self.last_kcp_packet_at: int | None = None
        self.last_payload_at: int | None = None
        self.last_frame_at: int | None = None
        self.first_payloads: list[dict[str, Any]] = []
        self._last_stats_frame_count = -1
        self._last_stats_payload_bucket = -1

    def close(self) -> None:
        if self.h264_stream is not None:
            self.h264_stream.close()
            self.h264_stream = None
        if self.g711_stream is not None:
            self.g711_stream.close()
            self.g711_stream = None

    def receive_packet(self, packet: UdpPacket, addr: tuple[str, int]) -> None:
        if self.error is not None:
            return
        now = int(time.time())
        self.udp_packets += 1
        self.last_packet_at = now
        if packet.packet_type not in {
            P2PPacketType.KCP_DATA,
            P2PPacketType.DIRECT_KCP_DATA,
            P2PPacketType.RELAY_KCP_DATA,
        }:
            return
        self.last_kcp_packet_at = now
        try:
            channels = self._channels_for_addr(addr, int(packet.packet_type))
            for channel in channels:
                for kcp_channel, payload in channel.receive_packet(packet):
                    self.kcp_payloads += 1
                    if kcp_channel != MEDIA_CHANNEL:
                        continue
                    self._handle_media_payload(payload)
                    self._emit_stats()
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            self._emit_stats()

    def update(self) -> None:
        """Flush pending KCP ACKs on active media paths."""

        for channel in self.paths.values():
            channel.update()

    def as_dict(self) -> dict[str, Any]:
        return {
            "error": self.error,
            "paths": [
                {"host": host, "port": port, "packet_type": packet_type, "mode": mode}
                for host, port, packet_type, mode in self.paths
            ],
            "kcp_ack_datagrams": sum(channel.ack_stats()["datagrams"] for channel in self.paths.values()),
            "kcp_ack_segments": sum(channel.ack_stats()["segments"] for channel in self.paths.values()),
            "udp_packets": self.udp_packets,
            "kcp_payloads": self.kcp_payloads,
            "app_packets": self.app_packets,
            "frames": self.frames,
            "h264_frames": self.h264_frames,
            "g711_frames": self.g711_frames,
            "jpeg_frames": self.jpeg_frames,
            "last_packet_at": self.last_packet_at,
            "last_kcp_packet_at": self.last_kcp_packet_at,
            "last_payload_at": self.last_payload_at,
            "last_frame_at": self.last_frame_at,
            "first_payloads": self.first_payloads[:10],
        }

    def _channels_for_addr(self, addr: tuple[str, int], packet_type: int) -> list[Any]:
        if packet_type == P2PPacketType.RELAY_KCP_DATA:
            return [self._channel(addr, int(P2PPacketType.DIRECT_KCP_DATA), "relay")]
        if packet_type == P2PPacketType.DIRECT_KCP_DATA:
            return [self._channel(addr, int(P2PPacketType.RELAY_KCP_DATA), "relay")]
        return [self._channel(addr, int(P2PPacketType.KCP_DATA), "direct")]

    def _channel(self, addr: tuple[str, int], packet_type: int, mode: str) -> Any:
        key = (addr[0], addr[1], packet_type, mode)
        if key not in self.paths:
            from .live_kcp import XHomeKcpChannels

            def send_udp(data: bytes, address: tuple[str, int] = addr) -> None:
                self.sock.sendto(data, address)

            self.paths[key] = XHomeKcpChannels(
                uid=self.uid,
                send_udp=send_udp,
                relay_tunnel=mode == "relay",
                outbound_packet_type=packet_type,
                uid_suffix=self.uid if mode == "relay" else None,
            )
        return self.paths[key]

    def _handle_media_payload(self, payload: bytes) -> None:
        if len(self.first_payloads) < 10:
            self.first_payloads.append(native_payload_summary(payload))
        try:
            packet = parse_live_app_media_packet(payload)
        except ValueError:
            return
        self.app_packets += 1
        self.last_payload_at = int(time.time())
        frame = self.assembler.feed(packet)
        if frame is None:
            return
        self.frames += 1
        self.last_frame_at = int(time.time())
        if self.on_frame is not None:
            self.on_frame(frame)
        if frame.media_type in {MediaType.H264_I_FRAME, MediaType.H264_P_FRAME, MediaType.H264_B_FRAME}:
            self.h264_frames += 1
            if self.h264_stream is not None:
                self.h264_stream.write(frame.payload)
                self.h264_stream.flush()
        elif frame.media_type == MediaType.G711_AUDIO:
            self.g711_frames += 1
            if self.g711_stream is not None:
                self.g711_stream.write(frame.payload)
                self.g711_stream.flush()
        elif frame.media_type == MediaType.JPEG_FRAME:
            self.jpeg_frames += 1
            if self.jpeg_dir is not None:
                (self.jpeg_dir / f"frame-{self.jpeg_frames:06d}.jpg").write_bytes(frame.payload)
        self._emit_stats()

    def _emit_stats(self) -> None:
        """Publish a compact snapshot of current media counters."""

        if self.on_stats is None:
            return
        payload_bucket = self.kcp_payloads // 50
        if (
            self.error is not None
            or self.frames != self._last_stats_frame_count
            or self.kcp_payloads <= 5
            or payload_bucket != self._last_stats_payload_bucket
        ):
            self._last_stats_frame_count = self.frames
            self._last_stats_payload_bucket = payload_bucket
            self.on_stats(self.as_dict())


def native_payload_summary(payload: bytes) -> dict[str, Any]:
    """Summarize one decoded native command payload."""

    summary: dict[str, Any] = {"payload_length": len(payload)}
    if len(payload) >= 8:
        command, payload_len = decode_native_frame_header(payload[:8])
        summary["native_command"] = command
        summary["native_payload_length"] = payload_len
        body = payload[8 : 8 + payload_len]
        if body:
            summary["native_payload_text"] = body.decode("utf-8", errors="replace")
    else:
        summary["payload_text"] = payload.decode("utf-8", errors="replace")
    return summary


def open_output(path: Path | None) -> BinaryIO | None:
    if path is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("wb")


def read_udp_available(sock: socket.socket, *, timeout: float) -> list[UdpPacket]:
    """Read UDP packets until one timeout window has no data."""

    return [packet for packet, _addr in read_udp_available_with_addresses(sock, timeout=timeout)]


def read_udp_available_with_addresses(
    sock: socket.socket,
    *,
    timeout: float,
    max_duration: float | None = None,
    max_packets: int | None = None,
) -> list[tuple[UdpPacket, tuple[str, int]]]:
    """Read UDP packets and source addresses until one timeout window has no data."""

    packets: list[tuple[UdpPacket, tuple[str, int]]] = []
    started_at = time.monotonic()
    old_timeout = sock.gettimeout()
    sock.settimeout(timeout)
    try:
        while True:
            try:
                data, addr = sock.recvfrom(4096)
            except TimeoutError:
                return packets
            except socket.timeout:
                return packets
            packets.append((decode_udp_packet(data), addr))
            if max_packets is not None and len(packets) >= max_packets:
                return packets
            if max_duration is not None and time.monotonic() - started_at >= max_duration:
                return packets
    finally:
        sock.settimeout(old_timeout)


def packet_summary(packet: UdpPacket) -> dict[str, Any]:
    """Return a JSON-serializable packet summary."""

    return {
        "type": packet.packet_type,
        "channel": packet.channel,
        "payload_length": len(packet.payload),
        "payload_text": packet.text if len(packet.payload) <= 4096 else packet.text[:4096],
    }


def packet_type_counts(packets: list[UdpPacket]) -> dict[str, int]:
    """Count observed UDP packets by type."""

    counts: dict[str, int] = {}
    for packet in packets:
        key = str(packet.packet_type)
        counts[key] = counts.get(key, 0) + 1
    return counts


def current_millis() -> int:
    """Return current Unix time in milliseconds."""

    return int(time.time() * 1000)


def int_or_none(value: Any) -> int | None:
    """Parse an integer-ish value."""

    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def best_effort_local_ip() -> str:
    """Return the likely outbound LAN IP without sending data."""

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def best_effort_local_ips() -> list[str]:
    """Return likely local candidate IPs for native type-6 rendezvous payloads."""

    addresses: list[str] = []
    first = best_effort_local_ip()
    if first != "127.0.0.1":
        addresses.append(first)
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, family=socket.AF_INET):
            host = info[4][0]
            if host and not host.startswith("127.") and host not in addresses:
                addresses.append(host)
    except OSError:
        pass
    return addresses or [first]
