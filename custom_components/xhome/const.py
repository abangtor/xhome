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

EVENT_XHOME_DOORBELL = "xhome_doorbell"
EVENT_XHOME_EVENT = "xhome_event"
EVENT_XHOME_PREFIX = "xhome_"

REGIONS = ["usa", "china", "europe", "test"]

PLATFORMS = [Platform.LOCK, Platform.BUTTON, Platform.SENSOR, Platform.BINARY_SENSOR, Platform.IMAGE]

SERVICE_REFRESH = "refresh"
