# XHome Live Sidecar

XHome live video is native P2P through `libIVIEWSAVAPIs.so`; it is not a REST,
HLS, or RTSP URL. The Home Assistant integration prepares a live token through
`xhome.prepare_live_stream`. This sidecar layer owns the next boundary:

1. receive live-token metadata from Home Assistant or the CLI
2. hand it to a native helper that can load `libIVIEWSAVAPIs.so`
3. send command `20` once P2P is connected
4. receive native callbacks
5. strip the 40-byte XHome media header
6. forward raw H.264/G.711/JPEG payloads to files, ffmpeg, or go2rtc

## Native Helper Contract

The native helper is intentionally tiny. It should run where the Android ARM64
library can load, for example Android/Termux or a purpose-built Android service.

Python writes JSON lines to helper stdin:

```json
{"action":"session","uid":"LSV...","token":"...","native_iot_host":"usaiotd.lancens.com","start_command":20,"stop_command":21,"media_header_bytes":40}
{"action":"send","cmd":20,"data_base64":""}
{"action":"stop","cmd":21}
```

The helper writes callback records to stdout:

```text
XHF1 + int32 callback_type + int32 command + int32 status + uint32 payload_len + payload
```

All integers are little-endian. The fields map directly to:

```java
IVIEWSAVAPIs.AVAPISCallback.callback(int type, int cmdOrType, int lenOrStatus, byte[] payload)
```

## Python Relay

The relay is available as a module:

```bash
python -m xhome.live_sidecar helper-contract
```

With a native helper:

```bash
python -m xhome.live_sidecar relay \
  --uid LSV212PFJU5TQT42R3UX \
  --token kzyhnn3nb6mx5aixhtirsh4p3nfwwhrd \
  --native-iot-host usaiotd.lancens.com \
  --bridge-command "/path/to/native-helper" \
  --h264-out /tmp/xhome.h264 \
  --g711-out /tmp/xhome.g711 \
  --duration 30
```

If the helper emits a saved callback stream, strip it offline:

```bash
python -m xhome.live_sidecar strip-callbacks callbacks.xhf \
  --h264-out /tmp/xhome.h264 \
  --g711-out /tmp/xhome.g711 \
  --jpeg-dir /tmp/xhome-jpeg
```

The resulting H.264 file is intentionally raw payload bytes. The next integration
step is to pipe those bytes into ffmpeg/go2rtc rather than writing files.

## Android Helper

`sidecar/android-java` contains a minimal Java helper for the native side of the
contract. It is not packaged as an Android app yet; it is meant to prove that the
original JNI library can be driven from a tiny process before we build service
packaging around it.
