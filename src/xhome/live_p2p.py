"""Portable UDP/P2P pieces for XHome live streaming."""

from __future__ import annotations

import json
import socket
import struct
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

UDP_HEADER = struct.Struct("<HHH")
CLIENT_CONTROL_CHANNEL = 1
MEDIA_CHANNEL = 2
RAW_CHANNEL = 3


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

    port = str(local_port)
    return build_json_payload(
        {
            "LocalIp": [{"IP": local_ip}],
            "Uid": uid,
            "Port": port,
            "Key": key if key is not None else port,
        }
    )


def build_peer_punch_payload(*, uid: str, port_token: str) -> bytes:
    """Build the client punching payload used by packet type 11."""

    return build_json_payload({"Uid": uid, "Key": "", "Port": port_token})


def build_peer_punch_response_payload(*, uid: str, port_token: str) -> bytes:
    """Build the response payload used by packet type 12."""

    return build_json_payload({"Time": current_millis(), "Uid": uid, "Key": "", "Port": port_token})


def build_uid_payload(*, uid: str, include_key: bool = False) -> bytes:
    """Build simple UID-bearing payloads used by heartbeat/relay-info packets."""

    payload = {"Uid": uid}
    if include_key:
        payload["Key"] = ""
    return build_json_payload(payload)


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

    def run(self, *, duration: float = 8.0, interval: float = 0.05) -> dict[str, Any]:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(("0.0.0.0", 0))
            sock.settimeout(self.timeout)
            local_ip = best_effort_local_ip()
            local_port = sock.getsockname()[1]
            port_token = str(local_port)
            connect_payload = build_client_connect_payload(uid=self.uid, local_ip=local_ip, local_port=local_port)
            punch_payload = build_peer_punch_payload(uid=self.uid, port_token=port_token)
            relay_info_payload = build_uid_payload(uid=self.uid, include_key=True)
            heartbeat_payload = build_uid_payload(uid=self.uid)
            packets: list[UdpPacket] = []
            responses: list[ClientConnectResponse] = []
            response_keys: set[str] = set()
            candidates: dict[tuple[str, int, P2PAddressKind], P2PAddress] = {}
            selected_peer: tuple[str, int] | None = None
            sent_counts: dict[str, int] = {
                "relay_touch": 0,
                "client_connect": 0,
                "direct_punch": 0,
                "punch_response": 0,
                "relay_info": 0,
                "heartbeat": 0,
            }

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
                    else:
                        sock.sendto(encode_udp_packet(P2PPacketType.DIRECT_PUNCH, punch_payload), candidate.address)
                        sent_counts["direct_punch"] += 1

                if selected_peer is not None:
                    sock.sendto(encode_udp_packet(P2PPacketType.HEARTBEAT, heartbeat_payload), selected_peer)
                    sent_counts["heartbeat"] += 1

                for received, addr in read_udp_available_with_addresses(sock, timeout=self.timeout):
                    packets.append(received)
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
                time.sleep(interval)

            return {
                "local_ip": local_ip,
                "local_port": local_port,
                "relays": [{"host": host, "port": port} for host, port in self.relays],
                "sent": sent_counts,
                "selected_peer": {"host": selected_peer[0], "port": selected_peer[1]} if selected_peer else None,
                "candidates": [candidate.as_dict() for candidate in candidates.values()],
                "client_connect_responses": [response.as_dict() for response in responses],
                "packet_type_counts": packet_type_counts(packets),
                "packets": [packet_summary(packet) for packet in packets[:10]],
                "packets_truncated": max(0, len(packets) - 10),
            }
        finally:
            sock.close()


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
