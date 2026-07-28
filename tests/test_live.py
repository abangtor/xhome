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
from xhome.live_sidecar import encode_callback_record, iter_callback_records, relay_callbacks
from xhome.live_p2p import build_client_connect_payload, decode_udp_packet, encode_udp_packet
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


if __name__ == "__main__":
    unittest.main()
