# Documentation

Architecture notes, reverse-engineering references, and Home Assistant design
documents live here.

Keep secrets, tokens, full device UIDs, and private media out of this directory.

HACS runtime packaging uses the vendored API client under
`custom_components/xhome/api`. Keep that copy in sync with `src/xhome` until the
API client is published as a separate package.
