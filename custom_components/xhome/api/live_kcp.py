"""KCP channel wrapper for XHome live streaming."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .live_p2p import (
    CLIENT_CONTROL_CHANNEL,
    MEDIA_CHANNEL,
    P2PPacketType,
    UdpPacket,
    encode_kcp_udp_packet,
)

CONTROL_CONV_ID = 0x11223344
MEDIA_CONV_ID = 0x11223345

SendUdp = Callable[[bytes], None]
KcpFactory = Callable[[int, Any], Any]


class MissingKcpDependency(RuntimeError):
    """Deprecated compatibility exception for older callers."""


@dataclass(frozen=True)
class KcpChannelConfig:
    """One native KCP channel definition."""

    channel: int
    conv_id: int


@dataclass(frozen=True)
class KcpAck:
    """One pending KCP ACK entry."""

    timestamp: int
    sequence: int


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
        self.outbound_datagrams = 0
        self.outbound_ack_segments = 0

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
        self.outbound_datagrams += 1
        self.outbound_ack_segments += count_ack_segments(kcp_payload)
        self.send_udp(
            encode_kcp_udp_packet(
                self.packet_type,
                kcp_payload,
                channel=self.config.channel,
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

    def ack_stats(self) -> dict[str, int]:
        """Return outbound ACK counters for diagnostics."""

        return {
            "datagrams": self.control.outbound_datagrams + self.media.outbound_datagrams,
            "segments": self.control.outbound_ack_segments + self.media.outbound_ack_segments,
        }


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


def count_ack_segments(payload: bytes) -> int:
    """Count ACK segments in a combined KCP payload."""

    count = 0
    offset = 0
    while offset + MinimalKCP.HEADER_BYTES <= len(payload):
        payload_len = int.from_bytes(payload[offset + 20 : offset + 24], "little", signed=False)
        body_end = offset + MinimalKCP.HEADER_BYTES + payload_len
        if body_end > len(payload):
            break
        if payload[offset + 4] == MinimalKCP.ACK:
            count += 1
        offset = body_end
    return count


def default_kcp_factory(conv_id: int, _identity_token: Any) -> Any:
    """Create the built-in minimal KCP implementation used by XHome live media."""

    return MinimalKCP(conv_id)


class MinimalKCP:
    """Small KCP subset sufficient for XHome live JPEG media.

    XHome media arrives as ordinary KCP PUSH segments where each KCP payload is
    already one complete app-media fragment. The receiver needs to return those
    payloads and send ACK segments so the door keeps publishing. This is not a
    general-purpose KCP implementation.
    """

    PUSH = 81
    ACK = 82
    HEADER_BYTES = 24
    RECEIVE_WINDOW = 32
    MTU_BYTES = 1400

    def __init__(self, conv_id: int) -> None:
        self.conv_id = conv_id
        self._outbound_handler: Callable[[Any, bytes], None] | None = None
        self._received: list[bytes] = []
        self._next_sequence = 0
        self._expected_receive_sequence: int | None = None
        self._pending_receive: dict[int, bytes] = {}
        self._pending_acks: list[KcpAck] = []
        self._last_ack_flush = time.monotonic()
        self.ack_batch_size = 3
        self.ack_max_datagram_bytes = self.MTU_BYTES
        self.ack_flush_interval = 0.01

    def include_outbound_handler(self, handler: Callable[[Any, bytes], None]) -> None:
        """Register a callback for outbound KCP segments."""

        self._outbound_handler = handler

    def enqueue(self, payload: bytes) -> None:
        """Queue and immediately emit one PUSH segment."""

        segment = self._encode_segment(self.PUSH, sequence=self._next_sequence, payload=payload)
        self._next_sequence += 1
        self._send(segment)

    def flush(self) -> None:
        """Compatibility no-op for the external KCP API."""

    def receive(self, data: bytes) -> None:
        """Process one or more KCP segments."""

        offset = 0
        while offset + self.HEADER_BYTES <= len(data):
            conv_id = int.from_bytes(data[offset : offset + 4], "little", signed=False)
            command = data[offset + 4]
            timestamp = int.from_bytes(data[offset + 8 : offset + 12], "little", signed=False)
            sequence = int.from_bytes(data[offset + 12 : offset + 16], "little", signed=False)
            payload_len = int.from_bytes(data[offset + 20 : offset + 24], "little", signed=False)
            body_start = offset + self.HEADER_BYTES
            body_end = body_start + payload_len
            if conv_id != self.conv_id or body_end > len(data):
                return
            if command == self.PUSH:
                self._queue_received(sequence, data[body_start:body_end])
                self._queue_ack(KcpAck(timestamp=timestamp, sequence=sequence))
            offset = body_end

    def get_all_received(self) -> list[bytes]:
        """Return and clear received app payloads."""

        received = self._received
        self._received = []
        return received

    def update(self, timestamp_ms: int | None = None) -> None:
        """Flush pending ACKs."""

        if not self._pending_acks:
            return
        if len(self._pending_acks) >= self.ack_batch_size or self._pending_ack_bytes() >= self.ack_max_datagram_bytes:
            self._flush_acks()
            return
        if time.monotonic() - self._last_ack_flush >= self.ack_flush_interval:
            self._flush_acks()

    def _encode_segment(
        self,
        command: int,
        *,
        timestamp: int = 0,
        sequence: int,
        una: int = 0,
        payload: bytes = b"",
        window: int | None = None,
    ) -> bytes:
        receive_window = self._receive_window() if window is None else window
        return (
            self.conv_id.to_bytes(4, "little")
            + bytes([command, 0])
            + receive_window.to_bytes(2, "little")
            + timestamp.to_bytes(4, "little", signed=False)
            + sequence.to_bytes(4, "little", signed=False)
            + una.to_bytes(4, "little", signed=False)
            + len(payload).to_bytes(4, "little", signed=False)
            + payload
        )

    def _queue_received(self, sequence: int, payload: bytes) -> None:
        """Buffer PUSH payloads and emit them in KCP sequence order."""

        if self._expected_receive_sequence is None:
            self._expected_receive_sequence = sequence
        if sequence < self._expected_receive_sequence:
            return
        self._pending_receive.setdefault(sequence, payload)
        while self._expected_receive_sequence in self._pending_receive:
            self._received.append(self._pending_receive.pop(self._expected_receive_sequence))
            self._expected_receive_sequence += 1

    def _send(self, segment: bytes) -> None:
        if self._outbound_handler is not None:
            self._outbound_handler(self, segment)

    def _queue_ack(self, ack: KcpAck) -> None:
        if self._pending_acks and self._pending_ack_bytes() + self.HEADER_BYTES > self.ack_max_datagram_bytes:
            self._flush_acks()
        self._pending_acks.append(ack)
        if len(self._pending_acks) >= self.ack_batch_size:
            self._flush_acks()

    def _flush_acks(self) -> None:
        if not self._pending_acks:
            return
        window = self._receive_window()
        una = self._expected_receive_sequence or 0
        pending = self._pending_acks
        self._pending_acks = []
        batch: list[bytes] = []
        batch_bytes = 0
        for ack in pending:
            segment = self._encode_segment(
                self.ACK,
                timestamp=ack.timestamp,
                sequence=ack.sequence,
                una=una,
                window=window,
            )
            if batch and batch_bytes + len(segment) > self.ack_max_datagram_bytes:
                self._send(b"".join(batch))
                batch = []
                batch_bytes = 0
            batch.append(segment)
            batch_bytes += len(segment)
        if batch:
            self._send(b"".join(batch))
        self._last_ack_flush = time.monotonic()

    def _pending_ack_bytes(self) -> int:
        return len(self._pending_acks) * self.HEADER_BYTES

    def _receive_window(self) -> int:
        queued = len(self._received) + len(self._pending_receive)
        return max(0, self.RECEIVE_WINDOW - queued)
