"""Portable UDP/P2P pieces for XHome live streaming."""

from __future__ import annotations

import json
import secrets
import socket
import struct
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Callable

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


def build_relay_touch_payload(*, uid: str, nonce: bytes | None = None) -> bytes:
    """Build the short channel-4 relay touch payload seen in the Android app.

    The native client sends packet type 18 on UDP envelope channel 4 with eight
    opaque bytes followed by the target UID. The relay echoes the opaque bytes
    back on packet type 19/channel 4 and sends a type-16 relay-connected notice.
    The bytes do not appear to be KCP; they are treated as an opaque nonce here.
    """

    nonce = nonce or secrets.token_bytes(8)
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
    ) -> None:
        if not relays:
            raise ValueError("At least one relay is required")
        self.uid = uid
        self.relays = relays
        self.timeout = timeout

    def run(
        self,
        *,
        duration: float = 8.0,
        interval: float = 0.05,
        kcp_start_command: int | None = None,
        kcp_start_interval: float = 0.5,
        on_ready: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(("0.0.0.0", 0))
            sock.settimeout(self.timeout)
            local_ip = best_effort_local_ip()
            local_ips = best_effort_local_ips()
            local_port = sock.getsockname()[1]
            port_token = str(local_port)
            connect_payload = build_client_connect_payload_for_ips(uid=self.uid, local_ips=local_ips, local_port=local_port)
            relay_info_payload = build_uid_payload(uid=self.uid, include_key=True)
            heartbeat_payload = build_uid_payload(uid=self.uid)
            relay_touch_payload = build_relay_touch_payload(uid=self.uid)
            packets: list[UdpPacket] = []
            responses: list[ClientConnectResponse] = []
            response_keys: set[str] = set()
            candidates: dict[tuple[str, int, P2PAddressKind], P2PAddress] = {}
            selected_peer: tuple[str, int] | None = None
            ready_notified = False
            sent_counts: dict[str, int] = {
                "relay_touch": 0,
                "client_connect": 0,
                "direct_punch": 0,
                "punch_response": 0,
                "relay_info": 0,
                "relay_touch_channel4": 0,
                "heartbeat": 0,
                "kcp_start": 0,
            }
            kcp_probe = KcpStartProbe(uid=self.uid, sock=sock, start_command=kcp_start_command)
            next_kcp_start = 0.0

            for relay in self.relays:
                sock.sendto(encode_udp_packet(P2PPacketType.RELAY_TOUCH), relay)
                sent_counts["relay_touch"] += 1

            deadline = time.monotonic() + duration
            while time.monotonic() < deadline:
                for relay in self.relays:
                    sock.sendto(encode_udp_packet(P2PPacketType.CLIENT_CONNECT, connect_payload), relay)
                    sent_counts["client_connect"] += 1

                for candidate in list(candidates.values()):
                    if candidate.kind == P2PAddressKind.RELAY:
                        sock.sendto(encode_udp_packet(P2PPacketType.RELAY_INFO, relay_info_payload), candidate.address)
                        sent_counts["relay_info"] += 1
                        sock.sendto(
                            encode_udp_packet(
                                P2PPacketType.DIRECT_KCP_DATA,
                                relay_touch_payload,
                                channel=RAW_CHANNEL,
                            ),
                            candidate.address,
                        )
                        sent_counts["relay_touch_channel4"] += 1
                    else:
                        punch_payload = build_peer_punch_payload(uid=self.uid, address_kind=candidate.kind)
                        sock.sendto(encode_udp_packet(P2PPacketType.DIRECT_PUNCH, punch_payload), candidate.address)
                        sent_counts["direct_punch"] += 1

                now = time.monotonic()
                if kcp_start_command is not None and now >= next_kcp_start:
                    sent_counts["kcp_start"] += kcp_probe.send_start_packets(candidates.values())
                    next_kcp_start = now + kcp_start_interval

                if selected_peer is not None:
                    sock.sendto(encode_udp_packet(P2PPacketType.HEARTBEAT, heartbeat_payload), selected_peer)
                    sent_counts["heartbeat"] += 1

                for received, addr in read_udp_available_with_addresses(sock, timeout=self.timeout):
                    packets.append(received)
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
                time.sleep(interval)

            return {
                "local_ip": local_ip,
                "local_port": local_port,
                "relays": [{"host": host, "port": port} for host, port in self.relays],
                "sent": sent_counts,
                "selected_peer": {"host": selected_peer[0], "port": selected_peer[1]} if selected_peer else None,
                "candidates": [candidate.as_dict() for candidate in candidates.values()],
                "client_connect_responses": [response.as_dict() for response in responses],
                "kcp_start_probe": kcp_probe.as_dict(),
                "packet_type_counts": packet_type_counts(packets),
                "packets": [packet_summary(packet) for packet in packets[:10]],
                "packets_truncated": max(0, len(packets) - 10),
            }
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


def read_udp_available(sock: socket.socket, *, timeout: float) -> list[UdpPacket]:
    """Read UDP packets until one timeout window has no data."""

    return [packet for packet, _addr in read_udp_available_with_addresses(sock, timeout=timeout)]


def read_udp_available_with_addresses(
    sock: socket.socket, *, timeout: float
) -> list[tuple[UdpPacket, tuple[str, int]]]:
    """Read UDP packets and source addresses until one timeout window has no data."""

    packets: list[tuple[UdpPacket, tuple[str, int]]] = []
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
