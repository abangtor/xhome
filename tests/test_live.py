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


if __name__ == "__main__":
    unittest.main()
