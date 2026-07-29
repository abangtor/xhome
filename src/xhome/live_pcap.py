"""PCAP helpers for XHome live-stream reverse engineering."""

from __future__ import annotations

import socket
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from .live import LiveAppMediaAssembler, LiveAppMediaFrame, parse_live_app_media_packet
from .live_p2p import MEDIA_CHANNEL, P2PPacketType, UdpPacket, decode_udp_packet

PCAP_GLOBAL_HEADER_BYTES = 24
PCAP_RECORD_HEADER_BYTES = 16
IPV4_UDP_PROTOCOL = 17
KCP_HEADER_BYTES = 24
KCP_PUSH_COMMAND = 81
MEDIA_PACKET_TYPES = {
    P2PPacketType.KCP_DATA,
    P2PPacketType.DIRECT_KCP_DATA,
    P2PPacketType.RELAY_KCP_DATA,
}


@dataclass(frozen=True)
class PcapUdpDatagram:
    """One IPv4 UDP datagram from a PCAP file."""

    timestamp: float
    source_host: str
    source_port: int
    destination_host: str
    destination_port: int
    payload: bytes


@dataclass(frozen=True)
class KcpSegment:
    """One KCP segment embedded in an XHome UDP packet."""

    conv_id: int
    command: int
    fragment: int
    window: int
    timestamp: int
    sequence: int
    una: int
    payload: bytes


@dataclass
class PcapMediaStats:
    """Extraction counters for a PCAP media pass."""

    udp_datagrams: int = 0
    xhome_packets: int = 0
    kcp_segments: int = 0
    app_packets: int = 0
    frames: int = 0
    h264_frames: int = 0
    g711_frames: int = 0
    jpeg_frames: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "udp_datagrams": self.udp_datagrams,
            "xhome_packets": self.xhome_packets,
            "kcp_segments": self.kcp_segments,
            "app_packets": self.app_packets,
            "frames": self.frames,
            "h264_frames": self.h264_frames,
            "g711_frames": self.g711_frames,
            "jpeg_frames": self.jpeg_frames,
        }


def iter_pcap_udp_datagrams(path: Path) -> Iterator[PcapUdpDatagram]:
    """Yield raw IPv4 UDP datagrams from a classic PCAP file.

    PCAPdroid exports raw-IP captures for VPN mode, while tcpdump commonly
    writes Ethernet frames. This parser supports both by detecting the link type
    in the global header.
    """

    with path.open("rb") as stream:
        magic = stream.read(4)
        if magic in {b"\xd4\xc3\xb2\xa1", b"M<\xb2\xa1"}:
            endian = "<"
        elif magic in {b"\xa1\xb2\xc3\xd4", b"\xa1\xb2<M"}:
            endian = ">"
        else:
            raise ValueError("Unsupported PCAP magic")
        rest = stream.read(PCAP_GLOBAL_HEADER_BYTES - 4)
        if len(rest) != PCAP_GLOBAL_HEADER_BYTES - 4:
            raise ValueError("PCAP global header is truncated")
        _version_major, _version_minor, _thiszone, _sigfigs, _snaplen, network = struct.unpack(
            endian + "HHIIII", rest
        )
        yield from _iter_pcap_records(stream, endian=endian, link_type=network)


def _iter_pcap_records(stream: BinaryIO, *, endian: str, link_type: int) -> Iterator[PcapUdpDatagram]:
    while True:
        record_header = stream.read(PCAP_RECORD_HEADER_BYTES)
        if not record_header:
            return
        if len(record_header) != PCAP_RECORD_HEADER_BYTES:
            raise ValueError("PCAP packet header is truncated")
        ts_sec, ts_usec, included_len, _original_len = struct.unpack(endian + "IIII", record_header)
        packet = stream.read(included_len)
        if len(packet) != included_len:
            raise ValueError("PCAP packet data is truncated")
        ip_packet = _extract_ipv4_packet(packet, link_type=link_type)
        if ip_packet is None:
            continue
        datagram = _parse_ipv4_udp(ts_sec + ts_usec / 1_000_000, ip_packet)
        if datagram is not None:
            yield datagram


def _extract_ipv4_packet(packet: bytes, *, link_type: int) -> bytes | None:
    if link_type == 101:
        return packet
    if link_type == 1 and len(packet) >= 14:
        ether_type = int.from_bytes(packet[12:14], "big")
        if ether_type == 0x0800:
            return packet[14:]
    return None


def _parse_ipv4_udp(timestamp: float, packet: bytes) -> PcapUdpDatagram | None:
    if len(packet) < 20 or packet[0] >> 4 != 4:
        return None
    header_len = (packet[0] & 0x0F) * 4
    if len(packet) < header_len + 8 or packet[9] != IPV4_UDP_PROTOCOL:
        return None
    source_host = socket.inet_ntoa(packet[12:16])
    destination_host = socket.inet_ntoa(packet[16:20])
    source_port, destination_port, udp_len, _checksum = struct.unpack("!HHHH", packet[header_len : header_len + 8])
    payload = packet[header_len + 8 : header_len + udp_len]
    return PcapUdpDatagram(
        timestamp=timestamp,
        source_host=source_host,
        source_port=source_port,
        destination_host=destination_host,
        destination_port=destination_port,
        payload=payload,
    )


def iter_xhome_udp_packets(path: Path) -> Iterator[tuple[PcapUdpDatagram, UdpPacket]]:
    """Yield decoded XHome UDP packets from a PCAP file."""

    for datagram in iter_pcap_udp_datagrams(path):
        if len(datagram.payload) < 6:
            continue
        try:
            yield datagram, decode_udp_packet(datagram.payload)
        except ValueError:
            continue


def parse_kcp_segment(data: bytes) -> KcpSegment:
    """Parse one KCP segment."""

    if len(data) < KCP_HEADER_BYTES:
        raise ValueError(f"KCP segment is too short: {len(data)} < {KCP_HEADER_BYTES}")
    conv_id = int.from_bytes(data[0:4], "little", signed=False)
    command = data[4]
    fragment = data[5]
    window = int.from_bytes(data[6:8], "little", signed=False)
    timestamp = int.from_bytes(data[8:12], "little", signed=False)
    sequence = int.from_bytes(data[12:16], "little", signed=False)
    una = int.from_bytes(data[16:20], "little", signed=False)
    payload_len = int.from_bytes(data[20:24], "little", signed=False)
    return KcpSegment(
        conv_id=conv_id,
        command=command,
        fragment=fragment,
        window=window,
        timestamp=timestamp,
        sequence=sequence,
        una=una,
        payload=data[KCP_HEADER_BYTES : KCP_HEADER_BYTES + payload_len],
    )


def iter_pcap_kcp_app_payloads(path: Path) -> Iterator[bytes]:
    """Yield command-8 app-media payloads from KCP media packets."""

    for _datagram, packet in iter_xhome_udp_packets(path):
        if packet.packet_type not in MEDIA_PACKET_TYPES or packet.channel != MEDIA_CHANNEL:
            continue
        try:
            segment = parse_kcp_segment(packet.payload)
        except ValueError:
            continue
        if segment.command == KCP_PUSH_COMMAND:
            yield segment.payload


def extract_pcap_media(
    path: Path,
    *,
    h264_out: Path | None = None,
    g711_out: Path | None = None,
    jpeg_dir: Path | None = None,
) -> PcapMediaStats:
    """Extract assembled live media frames from a successful app PCAP."""

    stats = PcapMediaStats()
    assembler = LiveAppMediaAssembler()
    if jpeg_dir:
        jpeg_dir.mkdir(parents=True, exist_ok=True)

    with _optional_output(h264_out) as h264_stream, _optional_output(g711_out) as g711_stream:
        for _datagram, packet in iter_xhome_udp_packets(path):
            stats.udp_datagrams += 1
            stats.xhome_packets += 1
            if packet.packet_type not in MEDIA_PACKET_TYPES or packet.channel != MEDIA_CHANNEL:
                continue
            try:
                segment = parse_kcp_segment(packet.payload)
            except ValueError:
                continue
            if segment.command != KCP_PUSH_COMMAND:
                continue
            stats.kcp_segments += 1
            frame = _feed_app_payload(assembler, segment.payload, stats)
            if frame is None:
                continue
            _write_frame(frame, stats, h264_stream=h264_stream, g711_stream=g711_stream, jpeg_dir=jpeg_dir)
    return stats


def _feed_app_payload(
    assembler: LiveAppMediaAssembler,
    payload: bytes,
    stats: PcapMediaStats,
) -> LiveAppMediaFrame | None:
    try:
        packet = parse_live_app_media_packet(payload)
    except ValueError:
        return None
    stats.app_packets += 1
    return assembler.feed(packet)


def _write_frame(
    frame: LiveAppMediaFrame,
    stats: PcapMediaStats,
    *,
    h264_stream: BinaryIO | None,
    g711_stream: BinaryIO | None,
    jpeg_dir: Path | None,
) -> None:
    stats.frames += 1
    if frame.is_h264:
        stats.h264_frames += 1
        if h264_stream:
            h264_stream.write(frame.payload)
    elif frame.is_g711:
        stats.g711_frames += 1
        if g711_stream:
            g711_stream.write(frame.payload)
    elif frame.is_jpeg:
        stats.jpeg_frames += 1
        if jpeg_dir:
            (jpeg_dir / f"frame-{stats.jpeg_frames:06d}.jpg").write_bytes(frame.payload)


class _optional_output:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.stream: BinaryIO | None = None

    def __enter__(self) -> BinaryIO | None:
        if self.path is None:
            return None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("wb")
        return self.stream

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self.stream is not None:
            self.stream.close()
