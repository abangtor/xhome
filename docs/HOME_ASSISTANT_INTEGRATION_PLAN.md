# XHome Home Assistant Integration Plan

## Recommendation

Build a proper Home Assistant custom integration that imports the standalone
`xhome-api` Python package. Keep the cloud API wrapper independent, and make the
Home Assistant integration a thin adapter around it.

This is the best path for the real goal: controlling the door from Home
Assistant while still having a reusable client library for tests, scripts, and
future reverse-engineering work.

Avoid a Home Assistant `shell_command`, `command_line`, or service-only wrapper
except as a temporary smoke test. Those approaches are quicker, but they do not
fit Home Assistant's entity model, make credentials harder to handle cleanly,
and become awkward once we add battery, online state, events, media, and device
settings.

Do not start with an MQTT or sidecar service. A sidecar only becomes useful if
we later need native P2P video/control, BLE provisioning, or temporary password
generation through Android native libraries.

## Target Architecture

```text
Home Assistant
  custom_components/xhome/
    config_flow.py
    coordinator.py
    entity.py
    lock.py
    sensor.py
    binary_sensor.py
    switch.py
    number.py
    diagnostics.py
    services.yaml
        |
        v
xhome-api Python package
  xhome.client.XHomeClient
        |
        v
XHome/Lancens cloud REST API
```

The integration should use Home Assistant config entries for credentials and
state, then use `DataUpdateCoordinator` to poll the XHome cloud.

The current `xhome-api` client is synchronous and uses `requests`, so the Home
Assistant integration must call it via `hass.async_add_executor_job(...)` until
or unless the wrapper grows an async transport.

## Packaging Strategy

1. Keep `/home/openclaw/clawd/xhome-wrapper` as the standalone library project.
2. Add a separate Home Assistant integration directory, preferably
   `/home/openclaw/clawd/ha-xhome/custom_components/xhome` during development.
3. During local development, either install `xhome-api` into the Home Assistant
   Python environment or temporarily vendor the package into the integration.
4. For a clean long-term setup, publish/package `xhome-api` so
   `custom_components/xhome/manifest.json` can declare:

```json
{
  "requirements": ["xhome-api==0.1.0"]
}
```

If this becomes a HACS integration, the Home Assistant repository should contain
the `custom_components/xhome` integration and should depend on the packaged
library rather than duplicating the API code.

## Entity Model

### Device Registry

Create one Home Assistant device per XHome device.

Use the XHome `uid` as the stable unique identifier. Do not expose the full UID
in normal logs. Device metadata should include the XHome name, device id, model
or type, firmware when available, and manufacturer `XHome/Lancens`.

### Lock Entity

Expose door devices as `LockEntity`.

Initial behavior:

- `unlock()` calls `XHomeClient.unlock_door(uid)`.
- `lock()` does not call the cloud API. The guessed REST lock endpoint produced
  bad live side effects and should be treated as the wrong function unless
  later reverse engineering proves otherwise.
- State is kept locked because the cloud REST API has a confirmed unlock action
  but no confirmed authoritative locked-state read. This keeps Home Assistant's
  normal lock card offering the known-good unlock action repeatedly.

This gives Home Assistant the right control surface without pretending we know
more than the API actually tells us.

### Sensors

Useful first sensors:

- Battery percentage
- RSSI / signal strength
- Online type
- Firmware version
- DSP version when parsed from firmware text
- Unlock type / app lock status as diagnostics

### Binary Sensors

Useful first binary sensors:

- Online/offline

### Settings Entities

Expose low-risk settings as native writable Home Assistant entities:

- Screen light timeout as `NumberEntity`
- Main push and offline notifications as `SwitchEntity`
- Activity, doorbell-call, and lock-event notification categories as
  `SwitchEntity` controls over the app's `notify_ctrl` bitmask
- Battery display as `SwitchEntity`
- Call screen as `SwitchEntity`
- Wet-play setting as `SwitchEntity`
- Remote-unlock mode as `SwitchEntity` (`unlock_limit=0` means unlock anytime)
- Standby mode as `SelectEntity`
- Night vision target EV as `NumberEntity` when the device reports min/max EV
  bounds

Keep higher-risk or unclear operations out of the normal entity model:

- GMS/device config change
- App safety password changes
- Auth/password entry deletion
- Device deletion/share/transfer/account changes

Those can be developer services later, disabled or undocumented by default.

### Events And Media

The REST API can read events and media URLs, but there is no confirmed push
channel yet.

Plan:

- Poll event endpoints on a separate interval from device status.
- Deduplicate events by event GUID or id.
- Fire Home Assistant bus events such as `xhome_event` for new doorbell/motion
  records.
- Add an `ImageEntity` for the latest event image if `get_media_url(...)`
  returns a usable image URL.
- Do not expose live video as `CameraEntity` in the first version. Live video
  depends on native P2P libraries and needs separate reverse engineering.

## Config Flow

The integration should use a standard Home Assistant config flow.

Fields:

- Username
- Password
- Region selector: `usa`, `china`, `europe`, `test`
- Optional scan interval
- Optional event polling interval

Validation:

1. Log in with username/password.
2. Store credentials in the config entry.
3. Fetch device list.
4. Fail with `invalid_auth` if login fails.
5. Fail with `cannot_connect` on network or timeout errors.

Use a reauth flow when token expiration or auth errors are detected.

The live account currently works on the `usa` region. Do not assume region from
geography; validate it by login.

## Coordinator Design

Use one main coordinator per config entry.

Recommended polling:

- Device list/detail: every 60 seconds initially.
- Online status: every 30 to 60 seconds.
- Events/media: every 60 seconds, optional, disabled or slower if noisy.

The coordinator should collect:

- Device list
- Per-device detail
- Online status
- Screen config
- Optional latest event window

Keep API calls bounded and cached. Do not fetch media for every old event on
every poll.

## Services

Most user-facing control should be entities, not services.

Potential services:

- `xhome.refresh`: force coordinator refresh.
- `xhome.fetch_events`: fetch recent events for a selected device.
- `xhome.get_media_url`: resolve media for a selected event GUID.

Avoid a generic `xhome.unlock` service at first. Home Assistant already has
`lock.unlock`, and exposing a second unlock path increases accidental-trigger
risk.

## Security Rules

- Never write credentials into YAML examples, documentation, logs, BookStack, or
  git.
- Store credentials only in Home Assistant config entries or secrets.
- Redact tokens, usernames when appropriate, and full device UIDs in logs.
- Give all network requests explicit timeouts.
- Do not execute app-safety, GMS config, auth deletion, sharing, transfer, or
  device deletion operations from Home Assistant until they are deliberately
  designed and tested.
- Treat door unlock as a sensitive action. The integration can expose
  `lock.unlock`, but dashboards and automations should add their own
  confirmation or conditions.

## Implementation Phases

### Phase 1: Minimal Useful Integration

- Create `custom_components/xhome` skeleton.
- Add `manifest.json`, `const.py`, `config_flow.py`, translations, and config
  entry setup.
- Validate username/password login and device discovery.
- Add `DataUpdateCoordinator`.
- Add one `LockEntity` per door device.
- Add battery, RSSI, online, and firmware sensors.
- Add online binary sensor.
- Add diagnostics redacting token and full UID.

Outcome: Home Assistant can discover `MainDoor`, show useful state, and unlock
it through a native lock entity.

### Phase 2: Event And Media Support

- Poll recent event windows.
- Deduplicate newly seen events.
- Fire `xhome_event` on the Home Assistant event bus.
- Add latest-event sensors.
- Add latest-event `ImageEntity` if media URLs are stable.

Current implementation fires Home Assistant bus events from the polled
`v1/api/user/device/event/new` endpoint:

- `xhome_event` for every new record
- `xhome_doorbell` for ring/call-like records
- classified events such as `xhome_unlock`, `xhome_motion`,
  `xhome_low_battery`, `xhome_lock`, `xhome_lock_event`, `xhome_alarm`,
  `xhome_tamper`, `xhome_offline`, and `xhome_online`
- a latest event sensor per device with the normalized event kind as state and
  redacted event metadata as attributes
- a latest event image entity per device for the latest image-bearing event

The Android app receives doorbell calls through mobile push providers
(`FirebaseMessagingService`, Huawei Push, Xiaomi/Oppo/Vivo receivers) and a
Lancens `PushInfo` path whose action is `call`. That is not implemented in Home
Assistant yet.

The latest-event image implementation resolves `v1/api/app/device/oss/list`
only for new image-bearing events and keeps signed URLs out of entity state.
Live video still depends on the native `IVIEWSAVAPIs` P2P stack and is not
implemented.

Outcome: Automations can react to XHome events without live P2P video.

### Phase 3: Safe Settings

- Add entities for screen timeout, notification controls, battery display,
  weather forecast, call-screen, remote-unlock mode, standby mode, and target
  EV settings.
- Add Home Assistant services for the recovered REST API helpers that do not
  fit naturally as entities: lock members, event member labels, raw
  temporary-password/auth records, app lock status, and direct setting setters.
- Add tests for each setter.
- Add options flow for polling intervals.

Outcome: Routine device settings and REST-backed member/auth metadata are
controllable from Home Assistant.

### Phase 4: Packaging And Hardening

- Add Home Assistant test harness using `pytest-homeassistant-custom-component`.
- Run `hassfest`.
- Add HACS metadata if we want easy installation.
- Publish or otherwise package `xhome-api` for Home Assistant dependency
  installation.
- Add reauth flow and better token-expiry handling.

Outcome: The integration is maintainable and installable instead of being a
local science project.

### Phase 5: Native Or Sidecar Work

Only start this if REST proves insufficient.

Possible future work:

- Native P2P live video/control sidecar.
- BLE provisioning helper.
- Temporary lock password generation via `IVIEWSPassword`.
- Push-notification bridge if a usable channel is found.

Outcome: richer device support, but with much higher complexity.

## First Build Checklist

- [ ] Create `ha-xhome/custom_components/xhome`.
- [ ] Package or vendor `xhome-api` for local Home Assistant testing.
- [ ] Implement config flow with region validation.
- [ ] Add coordinator using executor jobs.
- [ ] Add lock, battery, RSSI, firmware, and online entities.
- [ ] Test against the live account without running unlock automatically.
- [ ] Manually test unlock once only when explicitly requested.
- [ ] Add event polling after core entities are stable.

## Known Limitations

- The cloud REST API confirms unlock success, but does not yet give a reliable
  lock-state read.
- Live video and richer real-time control use native Android libraries and are
  out of scope for the first integration.
- The device currently reports `gms=0`; account-wide GMS list is empty, and
  device/model GMS reads returned `10009` in live testing.
- Temporary password/auth list, rename, delete, and raw-add submission are
  implemented. Human-friendly temporary password generation still needs the
  native `IVIEWSPassword`/`IVIEWSPSD` encoder or a reimplementation.
- Region must be validated by login. The tested account is in the `usa` API
  region even though the physical location is Malaysia.
