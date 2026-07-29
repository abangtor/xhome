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

The implemented Python path currently covers steps 1-5, passive KCP media
receive/ACK handling, live media dumps, and offline media extraction from
successful app PCAPs. Captures have shown both relay-shaped and direct-LAN KCP
media paths carrying JPEG frames. The remaining product gap is hardening that
receive path into a continuously served Home Assistant stream URL instead of
debug files.

## Portable Cloud Probe

The Python package includes a portable reimplementation of the native IoT TLS
login phase. It connects to the regional native IoT host on port `11201`, sends
command `10001` with `{"UID":"...","token":"..."}`, and reads native command
frames.

```bash
python -m xhome.live_sidecar cloud-probe \
  --uid LSV212PFJU5TQT42R3UX \
  --region usa \
  --insecure-skip-verify
```

The insecure flag is currently needed for the observed USA native host
certificate mismatch; the original Android library appears to tolerate that
mismatch.

`cloud-probe` and `mjpeg-server` can fetch their own live token. Pass
`--token` only when you already have a native live token from
`xhome.prepare_live_stream`. If `--token` is omitted, the sidecar logs in via
`XHOME_USERNAME`/`XHOME_PASSWORD`, `XHOME_TOKEN`, or the OpenClaw
`authProfiles.xhome` profile, then calls the REST live-token endpoint. The
native IoT host defaults from `--region`; override `--native-iot-host` only for
testing.

Add `--send-start` to deliberately send command `20` about one second after
login, matching the Android app's live-view timing. Command `21` is sent before
exit during controlled stream testing.

Add `--p2p-probe` with `--send-start` to send the first pure-Python UDP
client-connecting packets to the returned relay. This is still a probe, not a
complete KCP/media implementation.

Add `--p2p-rendezvous` with `--send-start` to continue farther into the native
client state machine. The rendezvous probe parses relay type-7 responses into
local/public/relay peer candidates, sends type-11 direct punch packets, sends
type-15 relay-info packets, answers type-11 peer handshakes with type-12
responses, sends the app's repeated four-packet type-18/channel-4 relay touch
bursts, and reports any type-13/18/19 KCP data packets it sees.

Add `--relay-only` only when the client and device are behind a path where the
direct local/public punches derail media. Official app captures on the same LAN
use direct packet type `13` media from the door to the phone.

Add `--kcp-start` to the rendezvous probe to actively send command `20` through
the recovered KCP path. The native TLS session is kept open while UDP rendezvous
runs; command `21` is sent only after the UDP/KCP probe exits.

```bash
python -m xhome.live_sidecar cloud-probe \
  --uid LSV212PFJU5TQT42R3UX \
  --region usa \
  --send-start \
  --p2p-rendezvous \
  --jpeg-dir /tmp/xhome-live-jpegs \
  --h264-out /tmp/xhome-live.h264 \
  --g711-out /tmp/xhome-live.g711 \
  --insecure-skip-verify
```

The live output options are debug dumps from the current receive path:

- `--jpeg-dir`: writes assembled JPEG frames as `frame-000001.jpg`, etc.
- `--h264-out`: appends raw H.264 payloads when the device sends media types
  `160/161/162`.
- `--g711-out`: appends raw G.711 audio payloads when the device sends media
  type `164`.

Native KCP packets use UDP packet type `13`, `18`, or `19`. The UDP envelope
channel is the native logical channel: `1` for control and `2` for media.
Packet type `18` with UDP channel `4` is a separate relay-touch packet: eight
opaque/timestamp-like bytes followed by the UID, echoed by the relay as packet
type `19`/channel `4`. KCP itself uses conversation id `0x11223344` for channel
1 and `0x11223345` for channel 2, with `nodelay(1, 10, 2, 1)`. Once
device-origin media is received, channel 2 carries command-`8` records with the
lower-level app-media fragmentation header; those fragments assemble into the
40-byte XHome media frames that can be stripped to H.264/G.711/JPEG.

The sidecar includes a KCP channel wrapper for those recovered parameters. The
compiled KCP dependency is intentionally optional and is not part of the Home
Assistant integration requirements:

```bash
pip install "xhome-api[live]"
```

## PCAP Media Extraction

`pcap-extract` validates the parser against successful Android app captures. It
supports PCAPdroid raw-IP captures and tcpdump Ethernet PCAPs:

```bash
python -m xhome.live_sidecar pcap-extract xhome-live-app.pcap \
  --jpeg-dir /tmp/xhome-jpegs \
  --h264-out /tmp/xhome.h264 \
  --g711-out /tmp/xhome.g711
```

The first successful capture decoded as relay packet type `19`, UDP channel `2`,
KCP conversation id `0x11223345`, KCP push command `81`, then command-`8`
app-media fragments. The observed live-view window yielded JPEG frames
(`media_type=165`), not H.264.

## Standalone MJPEG Debug Server

The Home Assistant custom component now embeds the portable native live path and
serves the `Live camera` entity directly. This standalone command is still
useful for debugging because the device has been publishing assembled JPEG
frames:

```bash
python -m xhome.live_sidecar mjpeg-server \
  --uid LSV212PFJU5TQT42R3UX \
  --region usa \
  --bind 0.0.0.0 \
  --port 8088 \
  --path /xhome.mjpeg \
  --duration 3600 \
  --insecure-skip-verify
```

For experiments, the Home Assistant XHome option `Live stream URL template` can
still point to this temporary URL:

```text
http://SIDECAR_HOST:8088/xhome.mjpeg
```

`mjpeg-server` attempts direct local/public punching by default, matching the
official app's same-LAN live-view capture. Add `--relay-only` to force the
older relay-only behavior while troubleshooting.

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
