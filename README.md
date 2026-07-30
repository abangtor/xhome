# XHome

Home Assistant custom integration and Python client for XHome/Lancens smart locks
and door devices.

## Status

This repository now contains the first Home Assistant project skeleton and the
reverse-engineered REST API wrapper. The API client has offline tests; the Home
Assistant integration has an initial config flow, coordinator, lock entity,
sensors, binary sensors, writable setting entities, latest event image entity,
embedded MJPEG live camera, diagnostics, API helper services, and a refresh
service.

The first supported path will be normal username/password auth only. Google,
WeChat, BLE provisioning, and native temporary password generation are out of
scope for the first Home Assistant version.

## Goal

Expose XHome door devices cleanly in Home Assistant:

- Door unlock through a native `LockEntity`
- Battery, RSSI, online, firmware, and diagnostic sensors
- Writable controls for routine device settings and remote-unlock configuration
- API helper services for lock members and temporary-password/auth records
- Latest event image through a native Home Assistant image entity
- Event/media polling where the cloud REST API supports it
- Embedded live camera entity using the native XHome P2P JPEG stream
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

The integration vendors its XHome REST and native live-stream client under
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
the runtime API and native live-stream client are vendored under
`custom_components/xhome/api`. The standalone `src/xhome` package remains useful
for CLI work, tests, and a future published package if we decide to split it
later.

The first Home Assistant integration should use `DataUpdateCoordinator` and call
the synchronous API client through Home Assistant executor jobs. The embedded
live camera uses the recovered native IoT/P2P transport directly; the standalone
sidecar CLI remains a debugging and packet-analysis tool.

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
Assistant bus events for new records. The optional local push listener feeds
push payloads into the same event path. Every classified event carries the same
payload as `xhome_event`.

| Event | When it fires | Notes |
| --- | --- | --- |
| `xhome_event` | Every new XHome event record. | Always fired first. |
| `xhome_doorbell` | Doorbell, ring, call, or indoor-call records. | Also fired for text records that look like a doorbell event. |
| `xhome_motion` | PIR/activity/motion records. | Includes lock activity events decoded from app lock payloads. |
| `xhome_unlock` | Unlock records. | Includes fingerprint, password, card, app, inside, temporary password, mechanical, Bluetooth, and other decoded unlock methods when known. |
| `xhome_lock` | Locking action records. | Includes locked, door locked, and remote lock. This is an actual locking action. |
| `xhome_lock_event` | Generic lock-device records. | Fallback when a lock-device record is visible but cannot be mapped to a more specific lock, unlock, alarm, doorbell, or user-management event. |
| `xhome_low_battery` | Low-battery records. | Covers ordinary low-power push types and decoded lock low-battery events. |
| `xhome_temperature_alarm` | Low/high temperature alarms. | Classified from app push types `8` and `9`. Also triggers `xhome_alarm`. |
| `xhome_sound_alarm` | Sound/noise alarm records. | Also triggers `xhome_alarm`. |
| `xhome_emergency` | Emergency/SOS records. | Also triggers `xhome_alarm`. |
| `xhome_smoke_alarm` | Decoded smoke alarm lock records. | Also triggers `xhome_alarm`. |
| `xhome_gas_alarm` | Decoded gas leakage lock records. | Also triggers `xhome_alarm`. |
| `xhome_tamper` | Tamper/demolition records. | Also triggers `xhome_alarm`. |
| `xhome_alarm` | Any alarm-like record. | Fired in addition to the more specific alarm event when available. |
| `xhome_offline` | Device offline records. | Classified from app push type `20`. |
| `xhome_online` | Device online records. | Classified from app push type `21`. |
| `xhome_transfer` | Transfer records. | Classified from app push type `100`. |
| `xhome_device_added` | Device-added records. | Classified from app push type `200`. |
| `xhome_refused` | Refused records. | Classified from app push type `201`. |
| `xhome_server_update` | Server-update records. | Classified from app push type `300`. |
| `xhome_user_added` | Decoded lock user-added records. | Uses decoded lock-event type `0x1E`. |
| `xhome_user_deleted` | Decoded lock user-deleted or user-cleared records. | Uses decoded lock-event types `0x1F` and `0x20`. |
| `xhome_mode_change` | Decoded lock mode-change records. | Includes away mode on/off. |

Event payload attributes:

| Attribute | Type | Description |
| --- | --- | --- |
| `device_name` | string | Human-readable XHome device name. |
| `device_id` | integer or `null` | XHome cloud device id when present. |
| `uid_tail` | string or `null` | Redacted device UID tail for diagnostics. |
| `event_key` | string | Stable integration dedupe key for the event. |
| `event_guid` | string or `null` | Raw XHome event GUID when present. |
| `event_id` | string or `null` | Raw XHome event id when present. |
| `event_type` | string or `null` | Raw XHome top-level event type, such as `1`, `6`, `20`, or `300`. |
| `event_type_name` | string or `null` | Decoded top-level event type name when known, such as `call`, `lock`, or `online`. |
| `event_kind` | string | Normalized integration kind, such as `doorbell`, `unlock`, `lock`, `alarm`, or `offline`. |
| `action` | string or `null` | Raw action text when supplied by XHome. |
| `time` | string or `null` | Raw event time text when supplied by XHome. |
| `time_stamp` | integer or `null` | Raw event timestamp when supplied by XHome. |
| `info` | string or `null` | Raw info field. Lock events may contain the app's base64 JSON payload here. |
| `name` | string or `null` | Raw event/device name field when supplied by XHome. |
| `remarks` | string or `null` | Raw remarks text when supplied by XHome. |
| `has_image` | boolean | Whether the record has an image URL or image reference. |
| `has_media` | boolean | Whether the record may have resolvable cloud media. |
| `video_status` | integer or `null` | Raw video status when supplied by XHome. |
| `video_size` | integer or `null` | Raw video size when supplied by XHome. |
| `source` | string | Event source, currently `poll` or `local_push`. |
| `lock_event_type` | string or `null` | Raw app lock-event type hex string from encoded lock payloads. |
| `lock_event_type_name` | string or `null` | Decoded app lock-event type, such as `unlock`, `locked`, `remote_lock`, or `add_user`. |
| `lock_event_content` | string or `null` | Raw app lock-event content hex string from encoded lock payloads. |
| `lock_event_content_name` | string or `null` | Decoded content value when known. Meaning depends on `lock_event_type_name`. |
| `lock_event_device` | string or `null` | Raw app lock-event device marker, such as `LOCK_PUSH`. |
| `lock_event_user_id` | string or `null` | Raw lock user id from the encoded app payload. |
| `lock_event_app_user` | string or `null` | App user from the encoded lock payload when present. |
| `lock_user_name` | string, omitted when unmapped | Friendly lock user name configured in the integration options. |
| `lock_person` | string, omitted when unmapped | Optional Home Assistant `person` entity configured for the mapped lock user. |

Lock user mapping:

Use **Settings > Devices & services > XHome > Configure** to add, edit, or
remove lock user mappings. The options flow keeps the existing general settings
in a separate **General settings** menu item and adds lock-user mapping screens.
Pick the lock, enter a friendly name, optionally choose a Home Assistant person,
and enter one or more lock user ids separated by commas, spaces, or new lines.
The edit path first asks which existing mapping to change, then opens the form
prefilled with the saved name, person, and ids.

The integration remembers recently observed unmapped `lock_event_user_id` values
while it is running and shows them on the mapping screen for the selected lock.
Add those ids to a mapping when you know who or what they represent. If an
event's id is not mapped, the event keeps the raw `lock_event_user_id` and omits
`lock_user_name` and `lock_person`.

Decoded `lock_event_content_name` values:

| Context | Raw `lock_event_type` | Raw `lock_event_content` | `lock_event_content_name` |
| --- | --- | --- | --- |
| Unlock method | `15` (`0x15`) | `00` | `fingerprint_unlock` |
| Unlock method | `15` (`0x15`) | `01` | `password_unlock` |
| Unlock method | `15` (`0x15`) | `02` | `card_unlock` |
| Unlock method | `15` (`0x15`) | `03` | `remote_control_unlock` |
| Unlock method | `15` (`0x15`) | `04` | `key_unlock` |
| Unlock method | `15` (`0x15`) | `05` | `iris_unlock` |
| Unlock method | `15` (`0x15`) | `06` | `palm_unlock` |
| Unlock method | `15` (`0x15`) | `07` | `finger_vein_unlock` |
| Unlock method | `15` (`0x15`) | `08` | `face_unlock` |
| Unlock method | `15` (`0x15`) | `09` | `app_unlock` |
| Unlock method | `15` (`0x15`) | `0A` | `inside_unlock` |
| Unlock method | `15` (`0x15`) | `0B` | `combination_unlock` |
| Unlock method | `15` (`0x15`) | `0C` | `temporary_password_unlock` |
| Unlock method | `15` (`0x15`) | `0D` | `mechanical_unlock` |
| Unlock method | `15` (`0x15`) | `0E` | `palm_print_unlock` |
| Unlock method | `15` (`0x15`) | `0F` | `virtual_password_unlock` |
| Unlock method | `15` (`0x15`) | `11` | `bluetooth_unlock` |
| Lock method | `13`/`14` (`0x13`/`0x14`) | `0A` | `inside_button_lock` |
| Lock method | `13`/`14` (`0x13`/`0x14`) | `13` | `outside_button_lock` |
| User add/delete/clear | `1E`/`1F`/`20` (`0x1E`/`0x1F`/`0x20`) | `00` | `fingerprint` |
| User add/delete/clear | `1E`/`1F`/`20` (`0x1E`/`0x1F`/`0x20`) | `01` | `password` |
| User add/delete/clear | `1E`/`1F`/`20` (`0x1E`/`0x1F`/`0x20`) | `02` | `card` |
| User add/delete/clear | `1E`/`1F`/`20` (`0x1E`/`0x1F`/`0x20`) | `03` | `remote_control` |
| User add/delete/clear | `1E`/`1F`/`20` (`0x1E`/`0x1F`/`0x20`) | `04` | `key` |
| User add/delete/clear | `1E`/`1F`/`20` (`0x1E`/`0x1F`/`0x20`) | `05` | `iris` |
| User add/delete/clear | `1E`/`1F`/`20` (`0x1E`/`0x1F`/`0x20`) | `06` | `palm` |
| User add/delete/clear | `1E`/`1F`/`20` (`0x1E`/`0x1F`/`0x20`) | `07` | `finger_vein` |
| User add/delete/clear | `1E`/`1F`/`20` (`0x1E`/`0x1F`/`0x20`) | `08` | `face` |
| User add/delete/clear | `1E`/`1F`/`20` (`0x1E`/`0x1F`/`0x20`) | `FF` | `all` |

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

Event processing resolves available OSS media automatically when XHome reports a
new event. The `Latest event image` entity and `Latest event video` sensor
expose only non-sensitive metadata; signed OSS URLs are not exposed in entity
state or diagnostics.

This is event media, not a live camera stream or a command to start recording.
Live viewing and active recording use the app's native P2P stack.

## Live streaming

The integration now exposes a per-device `Live camera` entity as the Home
Assistant-side surface for the native XHome P2P JPEG stream. XHome live video is
not a direct REST/HLS/RTSP URL; the official app logs in to the native IoT
service, discovers UDP peers through the XHome relay, then receives JPEG media
over KCP from the door.

No separate addon is required for the normal Home Assistant camera entity. When
Home Assistant opens the `Live camera`, the integration fetches a fresh live
token, starts the native rendezvous worker in-process, and serves the received
JPEG frames as MJPEG.

The camera entity always uses the embedded stream path and does not expose live
tokens in state. The old external bridge/URL-template setup path has been
removed from the Home Assistant integration.

Some Firefox/Home Assistant frontend combinations cancel long-running MJPEG
camera requests after roughly 30 seconds. For that case, the live camera exposes
`live_mjpeg_view_path`, a tokenized built-in viewer page that reconnects the
MJPEG image before the browser cancels it. Add that path to a Home Assistant
Webpage card when the normal camera card freezes but the live diagnostics show
frames are still being written.

The repo still includes standalone debugging tools in `xhome.live_sidecar` for
cloud probes, PCAP extraction, and temporary MJPEG serving outside Home
Assistant. They are for reverse engineering and troubleshooting, not normal HA
operation. See `docs/XHOME_LIVE_SIDECAR.md`.

The Home Assistant custom component includes the small KCP subset it needs to
ACK and reassemble the native media stream, so it does not require a separate
KCP wheel at setup time.

## Development Roadmap

1. Harden the config flow and coordinator with Home Assistant test coverage.
2. Test setup against a real Home Assistant instance without triggering unlock.
3. Manually test `lock.unlock` only when explicitly requested.
4. Add event/media polling.
5. Add hassfest, Home Assistant runtime tests, and release polish.
