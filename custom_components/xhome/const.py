"""Constants for the XHome Home Assistant integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "xhome"

CONF_EVENT_SCAN_INTERVAL = "event_scan_interval"
CONF_REGION = "region"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_TIMEOUT = "timeout"

DEFAULT_EVENT_SCAN_INTERVAL = 60
DEFAULT_REGION = "usa"
DEFAULT_SCAN_INTERVAL = 60
DEFAULT_TIMEOUT = 30

REGIONS = ["usa", "china", "europe", "test"]

PLATFORMS = [Platform.LOCK, Platform.SENSOR, Platform.BINARY_SENSOR]

SERVICE_REFRESH = "refresh"
