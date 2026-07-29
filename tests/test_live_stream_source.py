from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CAMERA_PATH = ROOT / "custom_components" / "xhome" / "camera.py"
CONST_PATH = ROOT / "custom_components" / "xhome" / "const.py"
COORDINATOR_PATH = ROOT / "custom_components" / "xhome" / "coordinator.py"
HACS_PATH = ROOT / "hacs.json"
STRINGS_PATH = ROOT / "custom_components" / "xhome" / "strings.json"
TRANSLATIONS_PATH = ROOT / "custom_components" / "xhome" / "translations" / "en.json"


class LiveStreamSourceTests(unittest.TestCase):
    def test_camera_platform_is_registered(self):
        const_source = CONST_PATH.read_text()
        hacs = json.loads(HACS_PATH.read_text())

        self.assertNotIn("CONF_LIVE_STREAM_URL_TEMPLATE", const_source)
        self.assertIn("Platform.CAMERA", const_source)
        self.assertIn("camera", hacs["domains"])

    def test_live_camera_uses_embedded_mjpeg_stream_without_token_attribute(self):
        camera_source = CAMERA_PATH.read_text()

        self.assertIn("class XHomeLiveCamera", camera_source)
        self.assertIn("async def handle_async_mjpeg_stream", camera_source)
        self.assertIn("async def async_camera_image", camera_source)
        self.assertIn("_last_live_jpeg", camera_source)
        self.assertIn("MJPEG_NEXT_FRAME_TIMEOUT", camera_source)
        self.assertIn("image_rotation_degrees", camera_source)
        self.assertIn("rotate_image_bytes", camera_source)
        self.assertIn("is_decodable_jpeg", camera_source)
        self.assertIn("prepare_live_image_bytes", camera_source)
        self.assertIn("def _rotate_jpeg", camera_source)
        self.assertIn("def _prepare_stream_jpeg", camera_source)
        self.assertIn("if frame is None:", camera_source)
        self.assertIn('"image_rotation": self._image_rotation()', camera_source)
        self.assertIn('"live_rotation_edge_crop_mode": "auto"', camera_source)
        self.assertIn('"live_rotation_edge_crop_pixels": self._live_rotation_edge_crop_pixels', camera_source)
        self.assertIn('"live_rotated_frames": self._live_rotated_frames', camera_source)
        self.assertIn('"live_rotation_failures": self._live_rotation_failures', camera_source)
        self.assertIn('"live_invalid_jpeg_frames": self._live_invalid_jpeg_frames', camera_source)
        self.assertIn('"live_p2p_udp_packets": self._live_transport_stats.get("udp_packets")', camera_source)
        self.assertIn(
            '"live_p2p_kcp_ack_datagrams": self._live_transport_stats.get("kcp_ack_datagrams")',
            camera_source,
        )
        self.assertIn('"live_p2p_kcp_ack_segments": self._live_transport_stats.get("kcp_ack_segments")', camera_source)
        self.assertIn('"live_p2p_raw_channel_kcp_segments"', camera_source)
        self.assertIn("_attr_frame_interval = 0.2", camera_source)
        self.assertNotIn("CameraEntityFeature.STREAM", camera_source)
        self.assertIn("async def stream_source", camera_source)
        self.assertIn("XHomeLiveMjpegView", camera_source)
        self.assertIn("requires_auth = False", camera_source)
        self.assertIn("secrets.token_urlsafe", camera_source)
        self.assertIn("get_url(hass, prefer_external=False)", camera_source)
        self.assertIn("live_last_error", camera_source)
        self.assertIn("Timed out waiting for next live JPEG frame", camera_source)
        self.assertIn("live_p2p_sent_heartbeats", camera_source)
        self.assertIn("live_p2p_sent_direct_touches", camera_source)
        self.assertIn("live_p2p_last_packet_at", camera_source)
        self.assertIn("live_p2p_selected_peer", camera_source)
        self.assertIn("_NativeLiveControlKeeper", camera_source)
        self.assertIn("native_control.refresh_after_first_frame()", camera_source)
        self.assertIn('"live_native_control_start_refreshes"', camera_source)
        self.assertIn('"live_native_control_status_probes"', camera_source)
        self.assertIn('"live_native_control_device_setting_probes"', camera_source)
        self.assertIn('"live_native_control_last_command"', camera_source)
        self.assertIn('"live_native_control_last_device_setting_command"', camera_source)
        self.assertIn('"live_native_control_last_error"', camera_source)
        self.assertIn("NATIVE_CONTROL_POST_START_STATUS_COMMANDS", camera_source)
        self.assertIn("NATIVE_CONTROL_POST_START_DEVICE_COMMANDS", camera_source)
        self.assertIn("ControlCommand.GET_BATTERY_LEVEL_REQ", camera_source)
        self.assertIn("ControlCommand.GET_DEVICE_RSSI_REQ", camera_source)
        self.assertIn("ControlCommand.GET_RESOLUTION_REQ", camera_source)
        self.assertIn("ControlCommand.DEVICE_SET_CMD_GET_DEVICE_ROTATE_REQ", camera_source)
        self.assertIn("ControlCommand.DEVICE_SETTING_COMB_CMD", camera_source)
        self.assertIn("encode_device_setting_payload", camera_source)
        live_p2p_source = (ROOT / "src" / "xhome" / "live_p2p.py").read_text()
        live_kcp_source = (ROOT / "src" / "xhome" / "live_kcp.py").read_text()
        self.assertIn("interval: float = 0.01", live_p2p_source)
        self.assertIn("direct_touch_burst_size: int = 4", live_p2p_source)
        self.assertIn("heartbeat_interval: float = 2.0", live_p2p_source)
        self.assertIn("_normalize_raw_channel_kcp_packet", live_p2p_source)
        self.assertIn("prefix + packet.payload[8:]", live_p2p_source)
        self.assertIn("self.ack_max_datagram_bytes = self.MTU_BYTES", live_kcp_source)
        self.assertNotIn("render_live_stream_url", camera_source)
        self.assertNotIn("live_stream_url_template", camera_source)
        self.assertIn('"embedded_live_stream": True', camera_source)
        self.assertIn('"native_transport": "portable_p2p"', camera_source)
        self.assertIn("XHomeP2PRendezvousProbe", camera_source)
        self.assertNotIn('"token"', camera_source)

    def test_home_assistant_manifest_does_not_require_external_kcp_wheel(self):
        manifest = json.loads((ROOT / "custom_components" / "xhome" / "manifest.json").read_text())

        self.assertNotIn("kcp>=0.1.6", manifest["requirements"])

    def test_prepare_live_stream_fetches_native_token_metadata(self):
        coordinator_source = COORDINATOR_PATH.read_text()

        self.assertIn("class XHomeLiveStreamSession", coordinator_source)
        self.assertIn("self.client.get_device_token(uid=uid)", coordinator_source)
        self.assertIn("start_command: int = 20", coordinator_source)
        self.assertIn("media_header_bytes: int = 40", coordinator_source)
        self.assertIn("normalize_region(_entry_region(self.config_entry)).native_iot_host", coordinator_source)

    def test_live_stream_ui_strings_are_present_without_external_sidecar_config(self):
        strings = STRINGS_PATH.read_text()
        translations = TRANSLATIONS_PATH.read_text()

        for source in (strings, translations):
            self.assertNotIn("live_stream_url_template", source)
            self.assertIn("live_camera", source)


if __name__ == "__main__":
    unittest.main()
