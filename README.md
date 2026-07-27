# XHome

Home Assistant custom integration and Python client for XHome/Lancens smart locks
and door devices.

## Status

This repository now contains the first Home Assistant project skeleton and the
reverse-engineered REST API wrapper. The API client has offline tests; the Home
Assistant integration has an initial config flow, coordinator, lock entity,
sensors, binary sensors, diagnostics, and a refresh service.

The first supported path will be normal username/password auth only. Google,
WeChat, native P2P video/control, BLE provisioning, and temporary password
generation are out of scope for the first Home Assistant version.

## Goal

Expose XHome door devices cleanly in Home Assistant:

- Door unlock through a native `LockEntity`
- Battery, RSSI, online, firmware, and diagnostic sensors
- Event/media polling where the cloud REST API supports it
- Safe device settings after core state and unlock are stable

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

Door unlock is a sensitive operation. It should be exposed through Home
Assistant's standard `lock.unlock` path rather than a second generic service.

## Events

The integration polls XHome's recent-event REST endpoint and fires Home
Assistant bus events for new records:

- `xhome_event` for every new XHome event record
- `xhome_doorbell` for records that look like a doorbell/ring/call event

Example automation trigger:

```yaml
triggers:
  - trigger: event
    event_type: xhome_doorbell
```

This is polling-based. The Android app receives near-real-time doorbell calls
through mobile push providers and Lancens push hosts, but that push client path
has not been reimplemented for Home Assistant yet.

## Development Roadmap

1. Harden the config flow and coordinator with Home Assistant test coverage.
2. Test setup against a real Home Assistant instance without triggering unlock.
3. Manually test `lock.unlock` once only when explicitly requested.
4. Add event/media polling.
5. Add safe settings entities.
6. Add hassfest, Home Assistant runtime tests, and release polish.
