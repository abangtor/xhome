"""KCP channel wrapper for XHome live streaming."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .live_p2p import (
    CLIENT_CONTROL_CHANNEL,
    MEDIA_CHANNEL,
    P2PPacketType,
    RAW_CHANNEL,
    UdpPacket,
    encode_kcp_udp_packet,
)

CONTROL_CONV_ID = 0x11223344
MEDIA_CONV_ID = 0x11223345

SendUdp = Callable[[bytes], None]
KcpFactory = Callable[[int, Any], Any]


class MissingKcpDependency(RuntimeError):
    """Raised when the optional KCP dependency is unavailable."""


@dataclass(frozen=True)
class KcpChannelConfig:
    """One native KCP channel definition."""

    channel: int
    conv_id: int


CONTROL_CHANNEL_CONFIG = KcpChannelConfig(channel=CLIENT_CONTROL_CHANNEL, conv_id=CONTROL_CONV_ID)
MEDIA_CHANNEL_CONFIG = KcpChannelConfig(channel=MEDIA_CHANNEL, conv_id=MEDIA_CONV_ID)


class XHomeKcpChannel:
    """One KCP channel wrapped in the XHome UDP envelope."""

    def __init__(
        self,
        *,
        config: KcpChannelConfig,
        send_udp: SendUdp,
        packet_type: int,
        uid_suffix: str | None,
        kcp_factory: KcpFactory | None = None,
    ) -> None:
        self.config = config
        self.send_udp = send_udp
        self.packet_type = packet_type
        self.uid_suffix = uid_suffix
        self.kcp = (kcp_factory or default_kcp_factory)(config.conv_id, self)
        self.kcp.include_outbound_handler(self._send_kcp)

    def send(self, payload: bytes) -> None:
        """Queue application payload bytes for KCP delivery."""

        self.kcp.enqueue(payload)
        self.kcp.flush()

    def receive(self, kcp_payload: bytes) -> list[bytes]:
        """Feed one KCP segment and return all completed payloads."""

        self.kcp.receive(kcp_payload)
        return [bytes(item) for item in self.kcp.get_all_received()]

    def update(self, timestamp_ms: int | None = None) -> None:
        """Update KCP timers."""

        self.kcp.update(timestamp_ms)

    def _send_kcp(self, _kcp: Any, kcp_payload: bytes) -> None:
        self.send_udp(
            encode_kcp_udp_packet(
                self.packet_type,
                kcp_payload,
                channel=RAW_CHANNEL,
                uid_suffix=self.uid_suffix,
            )
        )


class XHomeKcpChannels:
    """Native channel-1/channel-2 KCP pair.

    Native uses channel 1 for control and channel 2 for media. When traffic goes
    through the relay tunnel, KCP UDP packets are packet type 18 and append the
    target UID after the KCP segment. Direct traffic uses packet type 13 without
    the UID suffix.
    """

    def __init__(
        self,
        *,
        uid: str,
        send_udp: SendUdp,
        relay_tunnel: bool,
        outbound_packet_type: int | None = None,
        uid_suffix: str | None = None,
        kcp_factory: KcpFactory | None = None,
    ) -> None:
        packet_type = (
            outbound_packet_type
            if outbound_packet_type is not None
            else P2PPacketType.DIRECT_KCP_DATA
            if relay_tunnel
            else P2PPacketType.KCP_DATA
        )
        uid_suffix = uid if uid_suffix is None and relay_tunnel else uid_suffix
        self.uid = uid
        self.relay_tunnel = relay_tunnel
        self.outbound_packet_type = packet_type
        self.uid_suffix = uid_suffix
        self.control = XHomeKcpChannel(
            config=CONTROL_CHANNEL_CONFIG,
            send_udp=send_udp,
            packet_type=packet_type,
            uid_suffix=uid_suffix,
            kcp_factory=kcp_factory,
        )
        self.media = XHomeKcpChannel(
            config=MEDIA_CHANNEL_CONFIG,
            send_udp=send_udp,
            packet_type=packet_type,
            uid_suffix=uid_suffix,
            kcp_factory=kcp_factory,
        )

    def send_control(self, payload: bytes) -> None:
        """Send channel-1 control payload bytes."""

        self.control.send(payload)

    def send_media(self, payload: bytes) -> None:
        """Send channel-2 media/control payload bytes."""

        self.media.send(payload)

    def receive_packet(self, packet: UdpPacket) -> list[tuple[int, bytes]]:
        """Feed one XHome UDP packet and return completed channel payloads."""

        if packet.packet_type not in {
            P2PPacketType.KCP_DATA,
            P2PPacketType.DIRECT_KCP_DATA,
            P2PPacketType.RELAY_KCP_DATA,
        }:
            return []
        payload = strip_uid_suffix(packet.payload, self.uid)
        conv_id = packet_conv_id(payload)
        if conv_id == self.control.config.conv_id:
            return [(self.control.config.channel, item) for item in self.control.receive(payload)]
        if conv_id == self.media.config.conv_id:
            return [(self.media.config.channel, item) for item in self.media.receive(payload)]
        return []

    def update(self, timestamp_ms: int | None = None) -> None:
        """Update both KCP channels."""

        self.control.update(timestamp_ms)
        self.media.update(timestamp_ms)


def strip_uid_suffix(payload: bytes, uid: str) -> bytes:
    """Remove the native relay-mode UID suffix when present."""

    suffix = uid.encode("ascii")
    if payload.endswith(suffix):
        return payload[: -len(suffix)]
    return payload


def packet_conv_id(payload: bytes) -> int | None:
    """Return the little-endian KCP conversation id from one segment."""

    if len(payload) < 4:
        return None
    return int.from_bytes(payload[:4], "little", signed=False)


def default_kcp_factory(conv_id: int, _identity_token: Any) -> Any:
    """Create a KCP object using the optional ``kcp`` package."""

    try:
        from kcp import KCP  # type: ignore[import-not-found]
    except ImportError as exc:
        raise MissingKcpDependency(
            "Install the optional live sidecar KCP dependency, for example `pip install kcp>=0.1.6`."
        ) from exc
    return KCP(
        conv_id,
        no_delay=True,
        update_interval=10,
        resend_count=2,
        no_congestion_control=True,
    )
