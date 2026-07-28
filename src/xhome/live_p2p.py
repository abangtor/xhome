"""Portable UDP/P2P probe pieces for XHome live streaming."""

from __future__ import annotations

import json
import socket
import struct
import time
from dataclasses import dataclass
from typing import Any

UDP_HEADER = struct.Struct("<HHH")


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


def encode_udp_packet(packet_type: int, payload: bytes = b"", *, channel: int = 0) -> bytes:
    """Encode the six-byte XHome P2P UDP header plus payload."""

    return UDP_HEADER.pack(packet_type, channel, 0) + payload


def decode_udp_packet(data: bytes) -> UdpPacket:
    """Decode an XHome P2P UDP packet."""

    if len(data) < UDP_HEADER.size:
        raise ValueError(f"UDP packet is too short: {len(data)}")
    packet_type, channel, _reserved = UDP_HEADER.unpack(data[: UDP_HEADER.size])
    return UdpPacket(packet_type=packet_type, channel=channel, payload=data[UDP_HEADER.size :])


def build_client_connect_payload(*, uid: str, local_ip: str, local_port: int) -> bytes:
    """Build the client-connecting JSON payload used by packet type 6."""

    return json.dumps(
        {
            "LocalIp": [{"IP": local_ip}],
            "Uid": uid,
            "Port": str(local_port),
            "Key": str(local_port),
        },
        separators=(",", ":"),
    ).encode("utf-8")


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
                sock.sendto(encode_udp_packet(6, payload), self.relay)
                packets.extend(read_udp_available(sock, timeout=self.timeout))
                time.sleep(interval)

            return {
                "local_ip": local_ip,
                "local_port": local_port,
                "relay": {"host": self.relay[0], "port": self.relay[1]},
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


def read_udp_available(sock: socket.socket, *, timeout: float) -> list[UdpPacket]:
    """Read UDP packets until one timeout window has no data."""

    packets: list[UdpPacket] = []
    old_timeout = sock.gettimeout()
    sock.settimeout(timeout)
    try:
        while True:
            try:
                data, _addr = sock.recvfrom(4096)
            except TimeoutError:
                return packets
            except socket.timeout:
                return packets
            packets.append(decode_udp_packet(data))
    finally:
        sock.settimeout(old_timeout)


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
