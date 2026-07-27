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
│   └── translations/             # UI strings for config/options flows
├── docs/                         # Architecture notes and API documentation
├── scripts/                      # Local development helpers
├── src/xhome/                    # Reusable Python API client package
├── tests/                        # Unit and Home Assistant integration tests
└── .github/workflows/            # CI workflows
```

## Local Development

Install the Python package into a Home Assistant development environment, then
copy or symlink `custom_components/xhome` into Home Assistant's
`custom_components` directory.

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

1. Harden the config flow and coordinator with Home Assistant test coverage.
2. Test setup against a real Home Assistant instance without triggering unlock.
3. Manually test `lock.unlock` once only when explicitly requested.
4. Add event/media polling.
5. Add safe settings entities.
6. Add HACS/hassfest/CI packaging.
