# XHome

Home Assistant custom integration and Python client for XHome/Lancens smart locks
and door devices.

## Status

This repository now contains the first Home Assistant project skeleton and the
reverse-engineered REST API wrapper. The API client has offline tests; the Home
Assistant integration has an initial config flow, coordinator, lock entity,
sensors, binary sensors, writable setting entities, latest event image entity,
diagnostics, API helper services, and a refresh service.

The first supported path will be normal username/password auth only. Google,
WeChat, native P2P video/control, BLE provisioning, and native temporary
password generation are out of scope for the first Home Assistant version.

## Goal

Expose XHome door devices cleanly in Home Assistant:

- Door unlock through a native `LockEntity`
- Battery, RSSI, online, firmware, and diagnostic sensors
- Writable controls for routine device settings and remote-unlock configuration
- API helper services for lock members and temporary-password/auth records
- Latest event image through a native Home Assistant image entity
- Manual latest event image/video download into Home Assistant media
- Event/media polling where the cloud REST API supports it
- Live camera entity surface for an external native P2P/go2rtc bridge
- Optional direct local push listener for near-real-time XHome events

## Planned Structure

```text
.
├── custom_components/xhome/      # Home Assistant custom integration
│   ├── api/                      # Vendored runtime API client for HACS installs
│   └── translations/             # UI strings for config/options flows
├── docs/                         # Architecture notes and API documentation
├── scripts/                      # Local development helpers
├── src/xhome/                    # Reusable Python API client package
├── tests/                        # Unit and Home Assistant integration tests
└── .github/workflows/            # CI workflows
```

## Local Development

### HACS Custom Repository

This repository can be installed as a HACS custom repository.

1. Open HACS in Home Assistant.
2. Open the three-dot menu and choose **Custom repositories**.
3. Add `https://github.com/abangtor/xhome`.
4. Select category **Integration**.
5. Install **XHome**.
6. Restart Home Assistant.
7. Add the integration from **Settings** -> **Devices & services** -> **Add integration** -> **XHome**.

The integration vendors its XHome REST API client under
`custom_components/xhome/api`, so a HACS install does not need a separate
`pip install -e .` step.

The repository includes the APK launcher icon as local brand assets under
`brand/icon.png` and `brand/logo.png` for HACS repository validation,
`custom_components/xhome/brand/icon.png` and
`custom_components/xhome/brand/logo.png` for Home Assistant's local custom
integration brand loader, plus `custom_components/xhome/icon.png` and
repository-root `icon.png` for older tooling that looks beside the manifest or
repository root.

### Manual Install

Copy `custom_components/xhome` into the Home Assistant config directory:

```text
config/
└── custom_components/
    └── xhome/
```

Restart Home Assistant and add the integration from the UI.

### Python Development

Install the standalone API package into a development environment when working
on the CLI or package tests:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
```

Run the API client tests:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

## Architecture

The integration should stay thin. Home Assistant code should handle config
entries, entities, polling, diagnostics, and services. For HACS installation,
the runtime API client is vendored under `custom_components/xhome/api`. The
standalone `src/xhome` package remains useful for CLI work, tests, and a future
published package if we decide to split it later.

The first Home Assistant integration should use `DataUpdateCoordinator` and call
the synchronous API client through Home Assistant executor jobs. A sidecar or
MQTT bridge is only worth adding later if native P2P, live video, BLE, or
temporary-password support becomes necessary.

## Security

Do not commit credentials, tokens, full device UIDs, or captured private media.
The integration must store credentials through Home Assistant config entries or
secrets and redact sensitive values from logs and diagnostics.

Door unlock operations are sensitive. The integration exposes the known-good
cloud unlock call through Home Assistant's standard `lock.unlock` path. The
cloud REST API does not expose a reliable locked-state read, and the guessed
cloud lock endpoint had bad live side effects, so `lock.lock` is intentionally
not wired to a cloud call.

## Settings

Routine XHome settings are exposed as writable Home Assistant entities where the
Android app uses simple REST setters:

- Push, offline, activity, doorbell-call, and lock-event notification switches
- Battery display, weather forecast, call screen, and remote-unlock mode switches
- Screen timeout number using the app's 5-60 second range
- Night vision target EV number when the device reports EV bounds
- Standby mode select with normal standby and trigger mode

Read-only/helper services are exposed for automation or Developer Tools use:

- `xhome.get_screen_light_config`
- `xhome.get_app_lock_status`
- `xhome.set_unlock_type`

## Lock Members And Temporary Passwords

The REST endpoints for lock members and temporary-password/auth records are
implemented in the Python client and exposed as Home Assistant services:

- `xhome.list_lock_members`
- `xhome.upsert_lock_member`
- `xhome.update_event_member`
- `xhome.list_temporary_passwords`
- `xhome.add_temporary_password`
- `xhome.add_temporary_password_raw`
- `xhome.rename_temporary_password`
- `xhome.delete_temporary_password`
- `xhome.prepare_live_stream`

`add_temporary_password` reimplements the Android `IVIEWSPassword` encoder from
`libIVIEWSPSD.so`: AES-CBC over the password using `uuid[4:20]` as key, a
16-character app-style `rand_key` as IV, and base64 ciphertext as `data`.
`add_temporary_password_raw` remains available for submitting an already encoded
`data` blob and `rand_key`. Access-changing services such as temporary-password
add/delete require `confirm: true`.

The CLI exposes the same split as `xhome auth-add --password ... --yes` and
`xhome auth-add-raw --data ... --rand-key ... --yes`.

## Events

The integration polls XHome's recent-event REST endpoint and fires Home
Assistant bus events for new records:

- `xhome_event` for every new XHome event record
- `xhome_doorbell` for records that look like a doorbell/ring/call event
- specific events such as `xhome_unlock`, `xhome_motion`,
  `xhome_low_battery`, `xhome_lock`, `xhome_lock_event`, `xhome_alarm`,
  `xhome_tamper`, `xhome_offline`, and `xhome_online` when the record can be
  classified

Example automation trigger:

```yaml
triggers:
  - trigger: event
    event_type: xhome_doorbell
```

This is polling-based. The Android app receives near-real-time doorbell calls
through mobile push providers and Lancens push hosts. The integration now also
has an optional local push listener that connects directly to the regional
Lancens push host on TLS port `11001`, registers the returned socket token with
XHome's token endpoints, and feeds command `3` push payloads into the same
`xhome_event` and classified event path used by polling. Polling remains enabled
as a fallback and dedupes against pushed event GUIDs.

Each device also has a `Last event` sensor. Its state is the normalized event
kind, and its attributes include the redacted event id/GUID, raw XHome type,
type name, timestamp, image/video flags, and decoded lock-event codes when the
app embeds them.

## Event Media

The integration exposes an `image` entity for each device's latest event image.
Some XHome event records contain a direct image URL, while others only contain
an `event_guid`; in that case the coordinator uses the app's OSS media endpoint
to resolve the signed image URL.

If the latest-event image is sideways, set the integration option `Latest event
image rotation` to `90`, `180`, or `270`. The option rotates the image bytes
served by the entity and does not change the camera/device configuration.

Each device also has a `Fetch latest event media` button. Pressing it polls the
latest cloud event for that device, resolves available OSS media, and saves the
latest image and any event video clip under Home Assistant's `media/xhome/...`
folder. The `Latest event image` entity and `Latest event video` sensor expose
only non-sensitive metadata and local media paths; signed OSS URLs are not
exposed in entity state or diagnostics.

This is event media, not a live camera stream or a command to start recording.
Live viewing and active recording use the app's native P2P stack.

## Live streaming

The integration now exposes a per-device `Live camera` entity as the Home
Assistant-side surface for a future/native XHome P2P bridge. XHome live video is
not a direct REST/HLS/RTSP URL; the Android app uses `libIVIEWSAVAPIs.so` to
obtain H.264/G.711 frames and then decodes them locally.

Set the integration option `Live stream URL template` to the stream URL produced
by an external bridge or go2rtc, for example:

```text
rtsp://homeassistant.local:8554/xhome/{uid_tail}
```

Supported placeholders are `{uid}`, `{uid_tail}`, `{device_id}`, and `{model}`.
The camera entity does not expose live tokens in state.

For bridge development, call the response service `xhome.prepare_live_stream`
with `uid` and `confirm: true`. It returns the live token, native IoT host, start
command `20`, stop command `21`, codec names, and the 40-byte media header size
needed by a sidecar. Treat that response like a secret; the live token is
credential material.

The repo now includes the first receive-only sidecar surface in
`xhome.live_sidecar`, plus a portable Python reimplementation of the native IoT
TLS login phase and the first UDP relay probe. The implemented Python path can
log in to `usaiotd.lancens.com:11201`, send command `20`, receive command `9`
with P2P relay addresses, talk to the returned UDP relay, parse peer candidates,
and run the native-shaped UDP rendezvous probe. Add `--kcp-start` to actively
send command `20` through the recovered KCP channel-2 path while the TLS live
session remains open. Device-origin media relay is still the remaining streaming
layer. See `docs/XHOME_LIVE_SIDECAR.md`.

The KCP wrapper is included in the Python package, but the compiled KCP binding
is optional and not installed by Home Assistant:

```bash
pip install "xhome-api[live]"
```

## Development Roadmap

1. Harden the config flow and coordinator with Home Assistant test coverage.
2. Test setup against a real Home Assistant instance without triggering unlock.
3. Manually test `lock.unlock` only when explicitly requested.
4. Add event/media polling.
5. Add hassfest, Home Assistant runtime tests, and release polish.
