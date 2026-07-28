from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from xhome.live import (
    CallbackType,
    ConnectionStatus,
    ControlCommand,
    LiveCallback,
    MediaType,
    callback_to_media_frame,
    live_session_from_token_payload,
    parse_media_frame,
)
from xhome.live_kcp import CONTROL_CONV_ID, MEDIA_CONV_ID, XHomeKcpChannels, strip_uid_suffix
from xhome.live_p2p import (
    P2PAddressKind,
    P2PPacketType,
    build_client_connect_payload,
    build_peer_punch_payload,
    build_peer_punch_response_payload,
    build_uid_payload,
    decode_udp_packet,
    encode_kcp_udp_packet,
    encode_udp_packet,
    parse_client_connect_responses,
)
from xhome.live_sidecar import encode_callback_record, iter_callback_records, relay_callbacks, unique_p2p_relays
from xhome.live_transport import (
    LIVE_LOGIN_COMMAND,
    decode_native_frame_header,
    encode_native_frame,
    extract_p2p_servers,
)


class LiveFrameTests(unittest.TestCase):
    def media_payload(self, media_type: int, payload: bytes = b"payload") -> bytes:
        header = bytearray(40)
        header[3] = media_type
        header[12:20] = (123456).to_bytes(8, "little")
        header[28:32] = (8000).to_bytes(4, "little")
        return bytes(header) + payload

    def test_parse_h264_media_frame(self):
        frame = parse_media_frame(self.media_payload(MediaType.H264_I_FRAME, b"\x00\x00\x00\x01abc"))

        self.assertEqual(frame.media_type, MediaType.H264_I_FRAME)
        self.assertEqual(frame.timestamp, 123456)
        self.assertEqual(frame.sample_rate, 8000)
        self.assertTrue(frame.is_h264)
        self.assertEqual(frame.payload, b"\x00\x00\x00\x01abc")

    def test_callback_to_media_frame_ignores_non_media_callback(self):
        callback = LiveCallback(
            callback_type=CallbackType.P2P_CONNECTION,
            command=0,
            status=ConnectionStatus.SUCCESS,
            payload=b"",
        )

        self.assertIsNone(callback_to_media_frame(callback))
        self.assertTrue(callback.is_ready)

    def test_callback_to_media_frame_parses_live_media_response(self):
        callback = LiveCallback(
            callback_type=CallbackType.IVIEWS_DATA,
            command=ControlCommand.LAN_GET_AV_DATA_RESP,
            status=0,
            payload=self.media_payload(MediaType.G711_AUDIO, b"audio"),
        )

        frame = callback_to_media_frame(callback)

        self.assertIsNotNone(frame)
        self.assertTrue(frame.is_g711)
        self.assertEqual(frame.payload, b"audio")

    def test_live_session_from_token_payload(self):
        metadata = live_session_from_token_payload(
            uid="LSV212PFJU5TQT42R3UX",
            native_iot_host="usaiotd.lancens.com",
            payload={"token": "abc123", "live": 1},
            device_id=587619,
        )

        self.assertEqual(metadata.token, "abc123")
        self.assertEqual(metadata.start_command, 20)
        self.assertEqual(metadata.as_bridge_payload()["media_header_bytes"], 40)


class LiveSidecarTests(unittest.TestCase):
    def media_payload(self, media_type: int, payload: bytes) -> bytes:
        header = bytearray(40)
        header[3] = media_type
        return bytes(header) + payload

    def test_callback_record_round_trip(self):
        callback = LiveCallback(
            callback_type=CallbackType.IVIEWS_DATA,
            command=ControlCommand.LAN_GET_AV_DATA_RESP,
            status=0,
            payload=b"abc",
        )

        decoded = list(iter_callback_records(io.BytesIO(encode_callback_record(callback))))

        self.assertEqual(decoded, [callback])

    def test_relay_writes_h264_and_g711_payloads(self):
        callbacks = iter(
            [
                LiveCallback(
                    callback_type=CallbackType.IVIEWS_DATA,
                    command=ControlCommand.LAN_GET_AV_DATA_RESP,
                    status=0,
                    payload=self.media_payload(MediaType.H264_P_FRAME, b"video"),
                ),
                LiveCallback(
                    callback_type=CallbackType.P2P_DATA,
                    command=ControlCommand.LAN_GET_AV_DATA_RESP,
                    status=0,
                    payload=self.media_payload(MediaType.G711_AUDIO, b"audio"),
                ),
            ]
        )

        with tempfile.TemporaryDirectory() as tmp:
            h264 = Path(tmp) / "out.h264"
            g711 = Path(tmp) / "out.g711"

            stats = relay_callbacks(callbacks=callbacks, command_sink=None, h264_out=h264, g711_out=g711)

            self.assertEqual(stats.h264_frames, 1)
            self.assertEqual(stats.g711_frames, 1)
            self.assertEqual(h264.read_bytes(), b"video")
            self.assertEqual(g711.read_bytes(), b"audio")


class LiveTransportTests(unittest.TestCase):
    def test_native_tls_frame_codec(self):
        payload = b'{"UID":"abc","token":"def"}'
        frame = encode_native_frame(LIVE_LOGIN_COMMAND, payload)

        command, payload_len = decode_native_frame_header(frame[:8])

        self.assertEqual(command, 10001)
        self.assertEqual(payload_len, len(payload))
        self.assertEqual(frame[8:], payload)

    def test_extract_p2p_servers_from_command_9(self):
        from xhome.live_transport import NativeFrame

        frames = [
            NativeFrame(command=1, payload=b""),
            NativeFrame(command=9, payload=b'[{"IP":"121.42.144.92","Port":"9729"}]'),
        ]

        self.assertEqual(extract_p2p_servers(frames), [{"IP": "121.42.144.92", "Port": "9729"}])

    def test_p2p_udp_packet_codec(self):
        data = encode_udp_packet(6, b"{}", channel=2)
        packet = decode_udp_packet(data)

        self.assertEqual(packet.packet_type, 6)
        self.assertEqual(packet.channel, 2)
        self.assertEqual(packet.payload, b"{}")

    def test_client_connect_payload_shape(self):
        payload = build_client_connect_payload(uid="LSV", local_ip="192.168.1.10", local_port=54321)
        decoded = __import__("json").loads(payload)

        self.assertEqual(decoded["Uid"], "LSV")
        self.assertEqual(decoded["Port"], "54321")
        self.assertEqual(decoded["Key"], "54321")
        self.assertEqual(decoded["LocalIp"], [{"IP": "192.168.1.10"}])

    def test_parse_client_connect_response_candidates(self):
        payload = (
            b'{"Uid":"LSV","PublicIp":"1.2.3.4","PublicPort":"5555","Online":"1","NatType":"0",'
            b'"PeerPublicIP":"5.6.7.8","PeerPublicPort":"6666",'
            b'"PeerLocalIP":[{"IP":"192.168.7.178"}],"PeerLocalPort":"7777",'
            b'"RelayAddress":[{"IP":"8.8.8.8","Port":"9792"}]}'
        )
        packet = decode_udp_packet(encode_udp_packet(P2PPacketType.CLIENT_CONNECT_RESPONSE, payload))

        response = parse_client_connect_responses([packet])[0]

        self.assertEqual(response.public_ip, "1.2.3.4")
        self.assertEqual(response.public_port, 5555)
        self.assertEqual(
            [(candidate.host, candidate.port, candidate.kind) for candidate in response.candidates],
            [
                ("192.168.7.178", 7777, P2PAddressKind.LOCAL),
                ("5.6.7.8", 6666, P2PAddressKind.PUBLIC),
                ("8.8.8.8", 9792, P2PAddressKind.RELAY),
            ],
        )

    def test_punch_and_heartbeat_payloads_match_native_field_names(self):
        punch = __import__("json").loads(build_peer_punch_payload(uid="LSV", port_token="54321"))
        punch_response = __import__("json").loads(
            build_peer_punch_response_payload(uid="LSV", port_token="54321")
        )
        relay_info = __import__("json").loads(build_uid_payload(uid="LSV", include_key=True))

        self.assertEqual(punch, {"Uid": "LSV", "Key": "", "Port": "54321"})
        self.assertEqual(punch_response["Uid"], "LSV")
        self.assertEqual(punch_response["Key"], "")
        self.assertEqual(punch_response["Port"], "54321")
        self.assertIsInstance(punch_response["Time"], int)
        self.assertEqual(relay_info, {"Uid": "LSV", "Key": ""})

    def test_kcp_udp_packet_appends_uid_for_direct_relay_mode(self):
        packet = decode_udp_packet(
            encode_kcp_udp_packet(
                P2PPacketType.DIRECT_KCP_DATA,
                b"kcp",
                channel=2,
                uid_suffix="LSV212PFJU5TQT42R3UX",
            )
        )

        self.assertEqual(packet.packet_type, P2PPacketType.DIRECT_KCP_DATA)
        self.assertEqual(packet.channel, 2)
        self.assertEqual(packet.payload, b"kcpLSV212PFJU5TQT42R3UX")

    def test_unique_p2p_relays_dedupes_command_9_servers(self):
        relays = unique_p2p_relays(
            [
                {"IP": "8.222.151.25", "Port": "9729"},
                {"IP": "8.222.151.25", "Port": "9729"},
                {"IP": "8.222.151.26", "Port": "9729"},
            ]
        )

        self.assertEqual(relays, [("8.222.151.25", 9729), ("8.222.151.26", 9729)])


class FakeKCP:
    def __init__(self, conv_id, identity_token):
        self.conv_id = conv_id
        self.identity_token = identity_token
        self.outbound = None
        self.received = []
        self.updated = False

    def include_outbound_handler(self, handler):
        self.outbound = handler

    def enqueue(self, payload):
        self.pending = payload

    def flush(self):
        self.outbound(self, b"kcp:" + self.pending)

    def receive(self, payload):
        self.received.append(payload)

    def get_all_received(self):
        pending = self.received
        self.received = []
        return pending

    def update(self, timestamp_ms=None):
        self.updated = True


class LiveKcpTests(unittest.TestCase):
    def test_kcp_channels_wrap_outbound_segments_in_relay_tunnel_packets(self):
        sent = []
        conv_ids = []

        def factory(conv_id, identity_token):
            conv_ids.append(conv_id)
            return FakeKCP(conv_id, identity_token)

        channels = XHomeKcpChannels(
            uid="LSV212PFJU5TQT42R3UX",
            send_udp=sent.append,
            relay_tunnel=True,
            kcp_factory=factory,
        )

        channels.send_media(b"payload")
        packet = decode_udp_packet(sent[0])

        self.assertEqual(conv_ids, [CONTROL_CONV_ID, MEDIA_CONV_ID])
        self.assertEqual(packet.packet_type, P2PPacketType.DIRECT_KCP_DATA)
        self.assertEqual(packet.channel, 4)
        self.assertEqual(packet.payload, b"kcp:payloadLSV212PFJU5TQT42R3UX")

    def test_kcp_channels_route_inbound_media_packets(self):
        channels = XHomeKcpChannels(
            uid="LSV212PFJU5TQT42R3UX",
            send_udp=lambda _data: None,
            relay_tunnel=True,
            kcp_factory=lambda conv_id, identity_token: FakeKCP(conv_id, identity_token),
        )
        packet = decode_udp_packet(
            encode_kcp_udp_packet(
                P2PPacketType.DIRECT_KCP_DATA,
                MEDIA_CONV_ID.to_bytes(4, "little") + b"frame",
                channel=4,
                uid_suffix="LSV212PFJU5TQT42R3UX",
            )
        )

        self.assertEqual(channels.receive_packet(packet), [(2, MEDIA_CONV_ID.to_bytes(4, "little") + b"frame")])

    def test_strip_uid_suffix_only_when_present(self):
        self.assertEqual(strip_uid_suffix(b"abcLSV", "LSV"), b"abc")
        self.assertEqual(strip_uid_suffix(b"abc", "LSV"), b"abc")


if __name__ == "__main__":
    unittest.main()
