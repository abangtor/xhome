from __future__ import annotations

import io
import socket
import ssl
import tempfile
import unittest
from pathlib import Path

from xhome.live import (
    CallbackType,
    ConnectionStatus,
    ControlCommand,
    LiveAppMediaAssembler,
    LiveCallback,
    MediaType,
    callback_to_media_frame,
    live_session_from_token_payload,
    parse_live_app_media_packet,
    parse_media_frame,
)
from xhome.live_kcp import CONTROL_CONV_ID, MEDIA_CONV_ID, MinimalKCP, XHomeKcpChannels, strip_uid_suffix
from xhome.live_p2p import (
    KcpMediaProbe,
    P2PAddressKind,
    P2PPacketType,
    RAW_CHANNEL,
    build_client_connect_payload,
    build_direct_touch_payload,
    build_peer_punch_payload,
    build_peer_punch_response_payload,
    build_relay_touch_nonce,
    build_relay_touch_payload,
    build_uid_payload,
    decode_udp_packet,
    encode_kcp_udp_packet,
    encode_udp_packet,
    parse_client_connect_responses,
    read_udp_available_with_addresses,
)
from xhome.live_pcap import extract_pcap_media
from xhome.live_sidecar import (
    LatestJpegBuffer,
    build_parser,
    encode_callback_record,
    iter_callback_records,
    relay_callbacks,
    unique_p2p_relays,
)
from xhome.live_transport import (
    LIVE_LOGIN_COMMAND,
    XHomeLiveCloudTransport,
    decode_native_frame_header,
    encode_device_setting_payload,
    encode_native_frame,
    extract_p2p_servers,
    make_ssl_context,
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

    def test_control_commands_include_native_status_probes(self):
        self.assertEqual(ControlCommand.GET_BATTERY_LEVEL_REQ, 114)
        self.assertEqual(ControlCommand.GET_RESOLUTION_REQ, 138)
        self.assertEqual(ControlCommand.GET_DEVICE_STATUS_REQ, 152)
        self.assertEqual(ControlCommand.GET_DEVICE_RSSI_REQ, 154)
        self.assertEqual(ControlCommand.DEVICE_SETTING_COMB_CMD, 1000)
        self.assertEqual(ControlCommand.DEVICE_SET_CMD_GET_DEVICE_ROTATE_REQ, 212)

    def test_parse_live_app_media_packet(self):
        header = bytearray(20)
        header[:4] = (8).to_bytes(4, "little")
        header[4:8] = (15).to_bytes(4, "little")
        header[11] = MediaType.JPEG_FRAME
        header[13] = 12
        header[14] = 1
        header[15] = 1
        header[16:20] = (3).to_bytes(4, "little")

        packet = parse_live_app_media_packet(bytes(header) + b"jpg")

        self.assertEqual(packet.command, 8)
        self.assertEqual(packet.declared_length, 15)
        self.assertEqual(packet.media_type, MediaType.JPEG_FRAME)
        self.assertEqual(packet.sequence, 12)
        self.assertEqual(packet.fragment_index, 1)
        self.assertTrue(packet.starts_frame)
        self.assertEqual(packet.payload, b"jpg")

    def test_parse_live_app_media_packet_uses_declared_length_for_final_jpeg_tail(self):
        jpeg_tail = b"body" + (b"x" * 26) + b"\xff\xd9"
        header = bytearray(20)
        header[:4] = (8).to_bytes(4, "little")
        header[4:8] = (len(jpeg_tail) + 12).to_bytes(4, "little")
        header[11] = MediaType.JPEG_FRAME
        header[15] = 2
        header[16:20] = (len(jpeg_tail) - 28).to_bytes(4, "little")

        packet = parse_live_app_media_packet(bytes(header) + jpeg_tail)

        self.assertTrue(packet.ends_frame)
        self.assertEqual(packet.payload, jpeg_tail)
        self.assertTrue(packet.payload.endswith(b"\xff\xd9"))

    def test_live_app_media_assembler_returns_frame_on_end_fragment(self):
        def packet(flag: int, index: int, payload: bytes) -> object:
            header = bytearray(20)
            header[:4] = (8).to_bytes(4, "little")
            header[4:8] = (len(payload) + 12).to_bytes(4, "little")
            header[11] = MediaType.JPEG_FRAME
            header[14] = index
            header[15] = flag
            header[16:20] = len(payload).to_bytes(4, "little")
            return parse_live_app_media_packet(bytes(header) + payload)

        assembler = LiveAppMediaAssembler()

        self.assertIsNone(assembler.feed(packet(1, 1, b"\xff\xd8")))
        self.assertIsNone(assembler.feed(packet(0, 2, b"body")))
        frame = assembler.feed(packet(2, 3, b"\xff\xd9"))

        self.assertIsNotNone(frame)
        self.assertTrue(frame.is_jpeg)
        self.assertEqual(frame.payload, b"\xff\xd8body\xff\xd9")

    def test_live_app_media_assembler_ignores_duplicate_fragments(self):
        def packet(flag: int, index: int, payload: bytes) -> object:
            header = bytearray(20)
            header[:4] = (8).to_bytes(4, "little")
            header[4:8] = (len(payload) + 12).to_bytes(4, "little")
            header[11] = MediaType.JPEG_FRAME
            header[14] = index
            header[15] = flag
            header[16:20] = len(payload).to_bytes(4, "little")
            return parse_live_app_media_packet(bytes(header) + payload)

        assembler = LiveAppMediaAssembler()

        self.assertIsNone(assembler.feed(packet(1, 1, b"\xff\xd8")))
        self.assertIsNone(assembler.feed(packet(0, 2, b"body")))
        self.assertIsNone(assembler.feed(packet(0, 2, b"duplicate")))
        frame = assembler.feed(packet(2, 3, b"\xff\xd9"))

        self.assertIsNotNone(frame)
        self.assertEqual(frame.payload, b"\xff\xd8body\xff\xd9")

    def test_live_app_media_assembler_drops_frames_with_fragment_gaps(self):
        def packet(flag: int, index: int, payload: bytes) -> object:
            header = bytearray(20)
            header[:4] = (8).to_bytes(4, "little")
            header[4:8] = (len(payload) + 12).to_bytes(4, "little")
            header[11] = MediaType.JPEG_FRAME
            header[14] = index
            header[15] = flag
            header[16:20] = len(payload).to_bytes(4, "little")
            return parse_live_app_media_packet(bytes(header) + payload)

        assembler = LiveAppMediaAssembler()

        self.assertIsNone(assembler.feed(packet(1, 1, b"\xff\xd8")))
        self.assertIsNone(assembler.feed(packet(0, 3, b"missing-index-2")))
        self.assertIsNone(assembler.feed(packet(2, 4, b"\xff\xd9")))


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

    def test_pcap_extract_writes_jpeg_frames(self):
        def app_payload(flag: int, index: int, payload: bytes) -> bytes:
            header = bytearray(20)
            header[:4] = (8).to_bytes(4, "little")
            header[4:8] = (len(payload) + 12).to_bytes(4, "little")
            header[11] = MediaType.JPEG_FRAME
            header[14] = index
            header[15] = flag
            header[16:20] = len(payload).to_bytes(4, "little")
            return bytes(header) + payload

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pcap = tmp_path / "sample.pcap"
            write_raw_ip_pcap(
                pcap,
                [
                    xhome_udp_kcp_datagram(app_payload(1, 1, b"\xff\xd8")),
                    xhome_udp_kcp_datagram(app_payload(2, 2, b"\xff\xd9")),
                ],
            )
            jpeg_dir = tmp_path / "jpeg"

            stats = extract_pcap_media(pcap, jpeg_dir=jpeg_dir)

            self.assertEqual(stats.jpeg_frames, 1)
            self.assertEqual((jpeg_dir / "frame-000001.jpg").read_bytes(), b"\xff\xd8\xff\xd9")

    def test_pcap_extract_accepts_direct_lan_kcp_media_packets(self):
        def app_payload(flag: int, index: int, payload: bytes) -> bytes:
            header = bytearray(20)
            header[:4] = (8).to_bytes(4, "little")
            header[4:8] = (len(payload) + 12).to_bytes(4, "little")
            header[11] = MediaType.JPEG_FRAME
            header[14] = index
            header[15] = flag
            header[16:20] = len(payload).to_bytes(4, "little")
            return bytes(header) + payload

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pcap = tmp_path / "sample.pcap"
            write_raw_ip_pcap(
                pcap,
                [
                    xhome_udp_kcp_datagram(app_payload(1, 1, b"\xff\xd8"), packet_type=P2PPacketType.KCP_DATA),
                    xhome_udp_kcp_datagram(app_payload(2, 2, b"\xff\xd9"), packet_type=P2PPacketType.KCP_DATA),
                ],
            )
            jpeg_dir = tmp_path / "jpeg"

            stats = extract_pcap_media(pcap, jpeg_dir=jpeg_dir)

            self.assertEqual(stats.jpeg_frames, 1)
            self.assertEqual((jpeg_dir / "frame-000001.jpg").read_bytes(), b"\xff\xd8\xff\xd9")

    def test_cloud_probe_can_fetch_live_token_when_token_is_omitted(self):
        args = build_parser().parse_args(
            [
                "cloud-probe",
                "--uid",
                "LSV212PFJU5TQT42R3UX",
                "--region",
                "usa",
                "--no-secrets",
                "--duration",
                "1",
            ]
        )

        self.assertIsNone(args.token)
        self.assertEqual(args.region, "usa")

    def test_mjpeg_server_uses_direct_punch_by_default(self):
        args = build_parser().parse_args(
            [
                "mjpeg-server",
                "--uid",
                "LSV212PFJU5TQT42R3UX",
                "--token",
                "token",
                "--native-iot-host",
                "usaiotd.lancens.com",
            ]
        )

        self.assertFalse(args.relay_only)

        relay_only = build_parser().parse_args(
            [
                "mjpeg-server",
                "--uid",
                "LSV212PFJU5TQT42R3UX",
                "--token",
                "token",
                "--native-iot-host",
                "usaiotd.lancens.com",
                "--relay-only",
            ]
        )

        self.assertTrue(relay_only.relay_only)

    def test_latest_jpeg_buffer_returns_new_frame_once(self):
        frames = LatestJpegBuffer()
        frames.update(b"\xff\xd8first")

        first = frames.wait_next(0, timeout=0)
        repeated = frames.wait_next(first[0], timeout=0) if first else None

        self.assertEqual(first, (1, b"\xff\xd8first"))
        self.assertIsNone(repeated)


class LiveTransportTests(unittest.TestCase):
    def test_native_tls_frame_codec(self):
        payload = b'{"UID":"abc","token":"def"}'
        frame = encode_native_frame(LIVE_LOGIN_COMMAND, payload)

        command, payload_len = decode_native_frame_header(frame[:8])

        self.assertEqual(command, 10001)
        self.assertEqual(payload_len, len(payload))
        self.assertEqual(frame[8:], payload)

    def test_native_device_setting_frame_codec(self):
        payload = encode_device_setting_payload(ControlCommand.DEVICE_SET_CMD_GET_DEVICE_ROTATE_REQ)
        frame = encode_native_frame(ControlCommand.DEVICE_SETTING_COMB_CMD, payload)

        command, payload_len = decode_native_frame_header(frame[:8])

        self.assertEqual(payload, b"\xd4\x00\x00\x00\x00\x00\x00\x00")
        self.assertEqual(command, 1000)
        self.assertEqual(payload_len, 8)
        self.assertEqual(frame[8:], payload)

    def test_native_tls_context_is_app_compatible(self):
        context = make_ssl_context(verify_tls=False)

        self.assertFalse(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_NONE)
        if hasattr(ssl, "TLSVersion"):
            self.assertEqual(context.maximum_version, ssl.TLSVersion.TLSv1_2)

    def test_extract_p2p_servers_from_command_9(self):
        from xhome.live_transport import NativeFrame

        frames = [
            NativeFrame(command=1, payload=b""),
            NativeFrame(command=9, payload=b'[{"IP":"121.42.144.92","Port":"9729"}]'),
        ]

        self.assertEqual(extract_p2p_servers(frames), [{"IP": "121.42.144.92", "Port": "9729"}])

    def test_read_available_returns_frames_collected_before_eof(self):
        metadata = live_session_from_token_payload(
            uid="LSV",
            native_iot_host="usaiotd.lancens.com",
            payload={"token": "token"},
        )
        transport = XHomeLiveCloudTransport(metadata)
        transport._socket = FakeSocket(encode_native_frame(9, b'[{"IP":"8.222.151.25","Port":"9729"}]'))

        frames = transport.read_available(duration=1)

        self.assertEqual([frame.command for frame in frames], [9])
        self.assertEqual(extract_p2p_servers(frames), [{"IP": "8.222.151.25", "Port": "9729"}])

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
        punch = __import__("json").loads(build_peer_punch_payload(uid="LSV", address_kind=P2PAddressKind.PUBLIC))
        punch_response = __import__("json").loads(
            build_peer_punch_response_payload(uid="LSV", port_token="54321")
        )
        relay_info = __import__("json").loads(build_uid_payload(uid="LSV", include_key=True))

        self.assertEqual(punch, {"Uid": "LSV", "Key": "", "Type": "1"})
        self.assertEqual(punch_response["Uid"], "LSV")
        self.assertEqual(punch_response["Key"], "")
        self.assertEqual(punch_response["Port"], "54321")
        self.assertIsInstance(punch_response["Time"], int)
        self.assertEqual(relay_info, {"Uid": "LSV", "Key": ""})

    def test_p2p_json_payloads_match_native_whitespace(self):
        uid = "LSV212PFJU5TQT42R3UX"

        self.assertEqual(build_uid_payload(uid=uid), b'{\n\t"Uid":\t"LSV212PFJU5TQT42R3UX"\n}')
        self.assertEqual(
            build_uid_payload(uid=uid, include_key=True),
            b'{\n\t"Uid":\t"LSV212PFJU5TQT42R3UX",\n\t"Key":\t""\n}',
        )
        self.assertEqual(
            build_peer_punch_payload(uid=uid, address_kind=P2PAddressKind.PUBLIC),
            b'{\n\t"Uid":\t"LSV212PFJU5TQT42R3UX",\n\t"Key":\t"",\n\t"Type":\t"1"\n}',
        )

    def test_relay_touch_payload_appends_uid_to_eight_byte_nonce(self):
        payload = build_relay_touch_payload(uid="LSV212PFJU5TQT42R3UX", nonce=b"12345678")

        self.assertEqual(payload, b"12345678LSV212PFJU5TQT42R3UX")

    def test_relay_touch_nonce_starts_with_unix_seconds(self):
        nonce = build_relay_touch_nonce(now=1_785_236_585, tick=607_005)

        self.assertEqual(len(nonce), 8)
        self.assertEqual(nonce[:4], (1_785_236_585).to_bytes(4, "little"))
        self.assertEqual(nonce[4:], (607_005).to_bytes(4, "little"))

    def test_direct_touch_payload_is_eight_byte_nonce_without_uid_suffix(self):
        payload = build_direct_touch_payload(nonce=b"12345678")

        self.assertEqual(payload, b"12345678")

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

    def test_direct_touch_uses_raw_channel_four_without_uid_suffix(self):
        packet = decode_udp_packet(
            encode_udp_packet(
                P2PPacketType.KCP_DATA,
                build_direct_touch_payload(nonce=b"12345678"),
                channel=4,
            )
        )

        self.assertEqual(packet.packet_type, P2PPacketType.KCP_DATA)
        self.assertEqual(packet.channel, 4)
        self.assertEqual(packet.payload, b"12345678")

    def test_kcp_channel_uses_logical_channel_in_udp_envelope(self):
        sent = []
        channels = XHomeKcpChannels(
            uid="LSV212PFJU5TQT42R3UX",
            send_udp=sent.append,
            relay_tunnel=True,
            kcp_factory=FakeKCP,
        )

        channels.send_media(b"start")
        packet = decode_udp_packet(sent[0])

        self.assertEqual(packet.packet_type, P2PPacketType.DIRECT_KCP_DATA)
        self.assertEqual(packet.channel, 2)

    def test_minimal_kcp_receives_push_payload_and_flushes_ack(self):
        sent = []
        kcp = MinimalKCP(MEDIA_CONV_ID)
        kcp.ack_flush_interval = 0
        kcp.include_outbound_handler(lambda _kcp, payload: sent.append(payload))
        push = minimal_kcp_push(sequence=7, timestamp=1234, payload=b"frame")

        kcp.receive(push)
        kcp.update()

        self.assertEqual(kcp.get_all_received(), [b"frame"])
        self.assertEqual(sent[0][:4], MEDIA_CONV_ID.to_bytes(4, "little"))
        self.assertEqual(sent[0][4], MinimalKCP.ACK)
        self.assertEqual(int.from_bytes(sent[0][8:12], "little"), 1234)
        self.assertEqual(int.from_bytes(sent[0][12:16], "little"), 7)
        self.assertEqual(int.from_bytes(sent[0][16:20], "little"), 8)

    def test_minimal_kcp_orders_payloads_and_drops_duplicates(self):
        sent = []
        kcp = MinimalKCP(MEDIA_CONV_ID)
        kcp.ack_flush_interval = 0
        kcp.include_outbound_handler(lambda _kcp, payload: sent.append(payload))

        kcp.receive(minimal_kcp_push(sequence=10, payload=b"first"))
        kcp.update()
        kcp.receive(minimal_kcp_push(sequence=12, payload=b"third"))
        kcp.update()
        kcp.receive(minimal_kcp_push(sequence=10, payload=b"duplicate"))
        kcp.update()
        self.assertEqual(kcp.get_all_received(), [b"first"])

        kcp.receive(minimal_kcp_push(sequence=11, payload=b"second"))
        kcp.update()

        self.assertEqual(kcp.get_all_received(), [b"second", b"third"])
        self.assertEqual([int.from_bytes(payload[16:20], "little") for payload in sent], [11, 11, 11, 13])

    def test_minimal_kcp_batches_acks_until_update(self):
        sent = []
        kcp = MinimalKCP(MEDIA_CONV_ID)
        kcp.include_outbound_handler(lambda _kcp, payload: sent.append(payload))

        kcp.receive(minimal_kcp_push(sequence=1, timestamp=100, payload=b"one"))
        kcp.receive(minimal_kcp_push(sequence=2, timestamp=200, payload=b"two"))
        self.assertEqual(sent, [])

        kcp.ack_flush_interval = 0
        kcp.update()

        self.assertEqual(len(sent), 1)
        self.assertEqual(len(sent[0]), 48)
        self.assertEqual([sent[0][4], sent[0][28]], [MinimalKCP.ACK, MinimalKCP.ACK])
        self.assertEqual([int.from_bytes(sent[0][12:16], "little"), int.from_bytes(sent[0][36:40], "little")], [1, 2])

    def test_unique_p2p_relays_dedupes_command_9_servers(self):
        relays = unique_p2p_relays(
            [
                {"IP": "8.222.151.25", "Port": "9729"},
                {"IP": "8.222.151.25", "Port": "9729"},
                {"IP": "8.222.151.26", "Port": "9729"},
            ]
        )

        self.assertEqual(relays, [("8.222.151.25", 9729), ("8.222.151.26", 9729)])

    def test_udp_reader_limits_packet_batch_to_avoid_keepalive_starvation(self):
        sock = FakeUdpSocket(
            [
                encode_udp_packet(P2PPacketType.HEARTBEAT, b"one"),
                encode_udp_packet(P2PPacketType.HEARTBEAT, b"two"),
                encode_udp_packet(P2PPacketType.HEARTBEAT, b"three"),
            ]
        )

        packets = read_udp_available_with_addresses(sock, timeout=0.2, max_packets=2)

        self.assertEqual([packet.payload for packet, _addr in packets], [b"one", b"two"])
        self.assertEqual(len(sock.datagrams), 1)

    def test_kcp_media_probe_reassembles_raw_channel_kcp_continuation(self):
        def app_payload(flag: int, index: int, payload: bytes) -> bytes:
            header = bytearray(20)
            header[:4] = (8).to_bytes(4, "little")
            header[4:8] = (len(payload) + 12).to_bytes(4, "little")
            header[11] = MediaType.JPEG_FRAME
            header[14] = index
            header[15] = flag
            header[16:20] = len(payload).to_bytes(4, "little")
            return bytes(header) + payload

        frames = []
        probe = KcpMediaProbe(uid="LSV212PFJU5TQT42R3UX", sock=FakeSendUdpSocket(), on_frame=frames.append)
        addr = ("192.168.7.178", 16904)
        start = minimal_kcp_push(sequence=1, payload=app_payload(1, 1, b"\xff\xd8"))
        end = minimal_kcp_push(sequence=2, payload=app_payload(2, 2, b"\xff\xd9"))

        probe.receive_packet(decode_udp_packet(encode_udp_packet(P2PPacketType.KCP_DATA, start, channel=2)), addr)
        probe.receive_packet(decode_udp_packet(encode_udp_packet(P2PPacketType.KCP_DATA, end[:8], channel=2)), addr)
        probe.receive_packet(
            decode_udp_packet(encode_udp_packet(P2PPacketType.KCP_DATA, b"nonce123" + end[8:], channel=RAW_CHANNEL)),
            addr,
        )

        self.assertEqual([frame.payload for frame in frames], [b"\xff\xd8\xff\xd9"])
        self.assertEqual(probe.raw_kcp_prefixes, 1)
        self.assertEqual(probe.raw_channel_kcp_segments, 1)
        self.assertEqual(probe.raw_channel_kcp_missing_prefixes, 0)

        default_frames = []
        default_probe = KcpMediaProbe(
            uid="LSV212PFJU5TQT42R3UX",
            sock=FakeSendUdpSocket(),
            on_frame=default_frames.append,
        )
        default_probe.receive_packet(
            decode_udp_packet(encode_udp_packet(P2PPacketType.KCP_DATA, start, channel=2)),
            addr,
        )
        default_probe.receive_packet(
            decode_udp_packet(encode_udp_packet(P2PPacketType.KCP_DATA, b"nonce123" + end[8:], channel=RAW_CHANNEL)),
            addr,
        )

        self.assertEqual([frame.payload for frame in default_frames], [b"\xff\xd8\xff\xd9"])
        self.assertEqual(default_probe.raw_channel_kcp_default_prefixes, 1)
        self.assertEqual(default_probe.raw_channel_kcp_missing_prefixes, 1)


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


class FakeSocket:
    def __init__(self, data: bytes) -> None:
        self.data = bytearray(data)
        self.timeout = None

    def recv(self, size: int) -> bytes:
        if not self.data:
            return b""
        chunk = self.data[:size]
        del self.data[:size]
        return bytes(chunk)

    def gettimeout(self):
        return self.timeout

    def settimeout(self, timeout):
        self.timeout = timeout


class FakeUdpSocket:
    def __init__(self, datagrams: list[bytes]) -> None:
        self.datagrams = list(datagrams)
        self.timeout = None

    def recvfrom(self, _size: int) -> tuple[bytes, tuple[str, int]]:
        if not self.datagrams:
            raise socket.timeout
        return self.datagrams.pop(0), ("127.0.0.1", 12345)

    def gettimeout(self):
        return self.timeout

    def settimeout(self, timeout):
        self.timeout = timeout


class FakeSendUdpSocket:
    def __init__(self) -> None:
        self.sent = []

    def sendto(self, data: bytes, addr: tuple[str, int]) -> None:
        self.sent.append((data, addr))


def minimal_kcp_push(*, sequence: int, payload: bytes, timestamp: int = 0) -> bytes:
    return (
        MEDIA_CONV_ID.to_bytes(4, "little")
        + bytes([MinimalKCP.PUSH, 0])
        + (32).to_bytes(2, "little")
        + timestamp.to_bytes(4, "little")
        + sequence.to_bytes(4, "little")
        + (0).to_bytes(4, "little")
        + len(payload).to_bytes(4, "little")
        + payload
    )


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
        self.assertEqual(packet.channel, 2)
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


def xhome_udp_kcp_datagram(
    app_payload: bytes,
    *,
    packet_type: P2PPacketType = P2PPacketType.RELAY_KCP_DATA,
) -> bytes:
    kcp = (
        MEDIA_CONV_ID.to_bytes(4, "little")
        + bytes([81, 0])
        + (32).to_bytes(2, "little")
        + (1).to_bytes(4, "little")
        + (1).to_bytes(4, "little")
        + (0).to_bytes(4, "little")
        + len(app_payload).to_bytes(4, "little")
        + app_payload
    )
    return encode_udp_packet(packet_type, kcp, channel=2)


def write_raw_ip_pcap(path: Path, udp_payloads: list[bytes]) -> None:
    records = bytearray()
    for index, udp_payload in enumerate(udp_payloads, start=1):
        source = bytes([8, 222, 151, 25])
        destination = bytes([10, 215, 173, 1])
        udp_header = (
            (9792).to_bytes(2, "big")
            + (58450).to_bytes(2, "big")
            + (8 + len(udp_payload)).to_bytes(2, "big")
            + b"\x00\x00"
        )
        ip_packet = (
            b"\x45\x00"
            + (20 + len(udp_header) + len(udp_payload)).to_bytes(2, "big")
            + b"\x00\x00\x00\x00\x40\x11\x00\x00"
            + source
            + destination
            + udp_header
            + udp_payload
        )
        records.extend((index).to_bytes(4, "little"))
        records.extend((0).to_bytes(4, "little"))
        records.extend(len(ip_packet).to_bytes(4, "little"))
        records.extend(len(ip_packet).to_bytes(4, "little"))
        records.extend(ip_packet)
    path.write_bytes(
        b"\xd4\xc3\xb2\xa1"
        + (2).to_bytes(2, "little")
        + (4).to_bytes(2, "little")
        + (0).to_bytes(4, "little")
        + (0).to_bytes(4, "little")
        + (65535).to_bytes(4, "little")
        + (101).to_bytes(4, "little")
        + bytes(records)
    )


if __name__ == "__main__":
    unittest.main()
