# Tests

Test coverage will be split into two layers:

- API client tests for signing, request shapes, response handling, and endpoint
  wrappers
- Home Assistant tests for config flow, coordinator updates, entities,
  diagnostics, and services

Live tests must never run unlock or other mutating commands unless explicitly
requested.

