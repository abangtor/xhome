# XHome Live Sidecar

XHome live video is native P2P; it is not a REST, HLS, or RTSP URL. The Home
Assistant integration prepares a live token through `xhome.prepare_live_stream`.
This sidecar layer reimplements the transport in portable Python.

1. receive live-token metadata from Home Assistant or the CLI
2. log in to the regional native IoT TLS endpoint on port `11201`
3. send command `20` to request AV
4. rendezvous with the returned UDP relay and complete the KCP session
5. strip the 40-byte XHome media header
6. forward raw H.264/G.711/JPEG payloads to files, ffmpeg, or go2rtc

The implemented Python path currently covers steps 1-3 plus the first UDP relay
probe. KCP session setup and media extraction are the next pieces.

## Portable Cloud Probe

The Python package includes a portable reimplementation of the native IoT TLS
login phase. It connects to the regional native IoT host on port `11201`, sends
command `10001` with `{"UID":"...","token":"..."}`, and reads native command
frames.

```bash
python -m xhome.live_sidecar cloud-probe \
  --uid LSV212PFJU5TQT42R3UX \
  --token ... \
  --native-iot-host usaiotd.lancens.com \
  --insecure-skip-verify
```

The insecure flag is currently needed for the observed USA native host
certificate mismatch; the original Android library appears to tolerate that
mismatch.

Add `--send-start` to deliberately send command `20` after login and command
`21` before exit during controlled stream testing.

Add `--p2p-probe` with `--send-start` to send the first pure-Python UDP
client-connecting packets to the returned relay. This is still a probe, not a
complete KCP/media implementation.

Add `--p2p-rendezvous` with `--send-start` to continue farther into the native
client state machine. The rendezvous probe parses relay type-7 responses into
local/public/relay peer candidates, sends type-11 direct punch packets, sends
type-15 relay-info packets, answers type-11 peer handshakes with type-12
responses, and reports any type-13/18/19 KCP data packets it sees.

Add `--kcp-start` to the rendezvous probe to actively send command `20` through
the recovered KCP path. The native TLS session is kept open while UDP rendezvous
runs; command `21` is sent only after the UDP/KCP probe exits.

```bash
python -m xhome.live_sidecar cloud-probe \
  --uid LSV212PFJU5TQT42R3UX \
  --token ... \
  --native-iot-host usaiotd.lancens.com \
  --send-start \
  --p2p-rendezvous \
  --kcp-start \
  --insecure-skip-verify
```

Native KCP packets use UDP packet type `13`, `18`, or `19` with UDP envelope
channel `4`. The KCP conversation id then identifies the logical channel:
`0x11223344` for channel 1 and `0x11223345` for channel 2, with
`nodelay(1, 10, 2, 1)`. Normal app commands, including command `20`, are sent as
the native 8-byte command frame over KCP channel 2. Once device-origin media is
received, channel 2 should carry command-`8` records with the 40-byte XHome
media header that can be stripped to H.264/G.711/JPEG.

The sidecar includes a KCP channel wrapper for those recovered parameters. The
compiled KCP dependency is intentionally optional and is not part of the Home
Assistant integration requirements:

```bash
pip install "xhome-api[live]"
```

## Callback Capture Format

The older `relay` and `strip-callbacks` commands are kept as a capture/debugging
format for callback records recovered from `libIVIEWSAVAPIs.so`. They are not
the preferred runtime path.

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

## Callback Tools

Print the callback contract:

```bash
python -m xhome.live_sidecar helper-contract
```

Relay a callback-emitting helper:

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
