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
        self.assertNotIn("Platform.BUTTON", const_source)
        self.assertIn("Platform.CAMERA", const_source)
        self.assertNotIn("button", hacs["domains"])
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
        self.assertIn('"live_mjpeg_clients_active": self._live_mjpeg_clients_active', camera_source)
        self.assertIn('"live_mjpeg_frames_written": self._live_mjpeg_frames_written', camera_source)
        self.assertIn('"live_mjpeg_last_end_reason": self._live_mjpeg_last_end_reason', camera_source)
        self.assertIn('"live_mjpeg_view_path": _native_mjpeg_view_path', camera_source)
        self.assertIn('"live_mjpeg_view_reconnect_seconds": MJPEG_VIEW_RECONNECT_INTERVAL', camera_source)
        self.assertIn('"live_startup_timings_ms": dict(self._live_startup_timings_ms)', camera_source)
        self.assertIn('self._live_startup_timings_ms = {"token_ready": token_ready_ms}', camera_source)
        self.assertIn("def _record_live_startup_timing", camera_source)
        self.assertIn('mark_timing("native_connected")', camera_source)
        self.assertIn('mark_timing("p2p_probe_started")', camera_source)
        self.assertIn('mark_timing("first_jpeg_frame")', camera_source)
        self.assertIn('self._record_live_startup_timing("first_mjpeg_frame_written")', camera_source)
        self.assertIn("lambda generation=stream_generation", camera_source)
        self.assertIn('"X-Accel-Buffering": "no"', camera_source)
        self.assertIn('"Content-Encoding": "identity"', camera_source)
        self.assertIn("_attr_frame_interval = 0.2", camera_source)
        self.assertNotIn("CameraEntityFeature.STREAM", camera_source)
        self.assertNotIn("_attr_supported_features = CameraEntityFeature.STREAM", camera_source)
        self.assertNotIn("async def stream_source", camera_source)
        self.assertIn("XHomeLiveMjpegView", camera_source)
        self.assertIn("XHomeLiveMjpegViewerView", camera_source)
        self.assertIn("requires_auth = False", camera_source)
        self.assertIn("secrets.token_urlsafe", camera_source)
        self.assertIn("MJPEG_VIEW_RECONNECT_INTERVAL = 20.0", camera_source)
        self.assertIn("MJPEG_VIEW_PROMOTE_TIMEOUT = 3.5", camera_source)
        self.assertIn("window.setTimeout(openStream, reconnectMs)", camera_source)
        self.assertIn("window.addEventListener(\"pagehide\"", camera_source)
        self.assertIn("def _native_mjpeg_path", camera_source)
        self.assertIn("def _native_mjpeg_view_path", camera_source)
        self.assertIn("def _native_mjpeg_view_html", camera_source)
        self.assertIn("return f\"/api/xhome/live/{entry_id}/{uid}/{token}.mjpeg\"", camera_source)
        self.assertIn("return f\"/api/xhome/live-view/{entry_id}/{uid}/{token}\"", camera_source)
        self.assertIn("live_last_error", camera_source)
        self.assertIn("Timed out waiting for next live JPEG frame", camera_source)
        self.assertIn("_NativeLiveControlKeeper", camera_source)
        self.assertIn("native_control.refresh_after_first_frame()", camera_source)
        self.assertNotIn("self._send_start_refresh()", camera_source)
        self.assertIn("NATIVE_CONTROL_POST_START_STATUS_COMMANDS", camera_source)
        self.assertIn("NATIVE_CONTROL_POST_START_DEVICE_COMMANDS", camera_source)
        self.assertIn("ControlCommand.GET_BATTERY_LEVEL_REQ", camera_source)
        self.assertIn("ControlCommand.GET_DEVICE_RSSI_REQ", camera_source)
        self.assertIn("ControlCommand.GET_RESOLUTION_REQ", camera_source)
        self.assertIn("ControlCommand.DEVICE_SET_CMD_GET_DEVICE_ROTATE_REQ", camera_source)
        self.assertIn("ControlCommand.DEVICE_SETTING_COMB_CMD", camera_source)
        self.assertIn("encode_device_setting_payload", camera_source)
        self.assertNotIn('"live_mjpeg_debug_path"', camera_source)
        self.assertNotIn('"live_mjpeg_last_request_path"', camera_source)
        self.assertNotIn('"live_mjpeg_last_user_agent"', camera_source)
        self.assertNotIn('"live_p2p_udp_packets"', camera_source)
        self.assertNotIn('"live_native_control_keepalives"', camera_source)
        self.assertNotIn("live_native_control_stop_commands", camera_source)
        self.assertNotIn("_live_transport_stats", camera_source)
        self.assertNotIn("def _peer_label", camera_source)
        self.assertNotIn('"token"', camera_source)

    def test_live_transport_matches_recovered_native_shape(self):
        camera_source = CAMERA_PATH.read_text()
        live_p2p_source = (ROOT / "src" / "xhome" / "live_p2p.py").read_text()
        live_kcp_source = (ROOT / "src" / "xhome" / "live_kcp.py").read_text()

        self.assertIn("interval: float = 0.01", live_p2p_source)
        self.assertIn("direct_touch_burst_size: int = 4", live_p2p_source)
        self.assertIn("relay_info_burst_size: int = 4", live_p2p_source)
        self.assertIn("relay_heartbeat_burst_size: int = 2", live_p2p_source)
        self.assertIn("relay_heartbeat_interval: float = 2.0", live_p2p_source)
        self.assertIn("heartbeat_interval: float = 2.0", live_p2p_source)
        self.assertIn('"relay_heartbeat": 0', live_p2p_source)
        self.assertIn('"peer_heartbeat": 0', live_p2p_source)
        self.assertIn("relay_info_bootstrapped", live_p2p_source)
        self.assertIn("candidate.kind == P2PAddressKind.LOCAL", live_p2p_source)
        self.assertIn("direct_touch_targets = {selected_peer}", live_p2p_source)
        self.assertIn("selected_peer is not None and selected_peer in relay_addresses", live_p2p_source)
        self.assertIn("self.direct_touch_echoes", live_p2p_source)
        self.assertIn("self.last_direct_touch_echo_at", live_p2p_source)
        self.assertIn("UNRELIABLE_MEDIA_CHANNEL = 3", live_p2p_source)
        self.assertIn("UNRELIABLE_MEDIA_MAGIC", live_p2p_source)
        self.assertIn("_handle_unreliable_media_payload", live_p2p_source)
        self.assertIn('"kcp_window_probe_requests"', live_p2p_source)
        self.assertIn("_normalize_raw_channel_kcp_packet", live_p2p_source)
        self.assertIn("prefix + packet.payload[8:]", live_p2p_source)
        self.assertIn("self.ack_batch_size = 3", live_kcp_source)
        self.assertIn("self.ack_max_datagram_bytes = self.MTU_BYTES", live_kcp_source)
        self.assertIn("WASK = 83", live_kcp_source)
        self.assertIn("WINS = 84", live_kcp_source)
        self.assertIn("_send_window_probe_response", live_kcp_source)
        self.assertIn("NATIVE_CONTROL_KEEPALIVE_INTERVAL = 12.0", camera_source)
        self.assertIn("NATIVE_CONTROL_READ_INTERVAL = 2.0", camera_source)
        self.assertIn("NATIVE_CONTROL_READ_DURATION = 0.005", camera_source)
        self.assertIn("_read_pending(duration=NATIVE_CONTROL_READ_DURATION)", camera_source)
        self.assertNotIn("render_live_stream_url", camera_source)
        self.assertNotIn("live_stream_url_template", camera_source)
        self.assertIn('"embedded_live_stream": True', camera_source)
        self.assertIn('"native_transport": "portable_p2p"', camera_source)
        self.assertIn("XHomeP2PRendezvousProbe", camera_source)

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
            self.assertNotIn("fetch_latest_event_media", source)
            self.assertIn("live_camera", source)


if __name__ == "__main__":
    unittest.main()
