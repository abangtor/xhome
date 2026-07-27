# XHome

Home Assistant custom integration and Python client for XHome/Lancens smart locks
and door devices.

## Status

This repository is freshly scaffolded. The reverse-engineered REST API wrapper
and Home Assistant integration will be moved in next.

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
│   └── translations/             # UI strings for config/options flows
├── docs/                         # Architecture notes and API documentation
├── scripts/                      # Local development helpers
├── src/xhome/                    # Reusable Python API client package
├── tests/                        # Unit and Home Assistant integration tests
└── .github/workflows/            # CI workflows
```

## Architecture

The integration should stay thin. Home Assistant code should handle config
entries, entities, polling, diagnostics, and services. The reusable `xhome`
Python package should handle login, signing, request/response handling, and the
XHome cloud REST endpoints.

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

## Development Roadmap

1. Move the existing `xhome-api` wrapper into `src/xhome`.
2. Add the Home Assistant `custom_components/xhome` skeleton.
3. Implement config flow login and device discovery.
4. Add lock, battery, RSSI, firmware, and online entities.
5. Add event/media polling.
6. Add safe settings entities.
7. Add tests, hassfest, and CI.

