"""Constants for Discovery Monitor."""

from typing import Final

DOMAIN: Final = "discovery_monitor"
EVENT_DISCOVERED: Final = "discovery_monitor_discovered"

CONF_ENABLED: Final = "enabled"
CONF_INCLUDE_SOURCES: Final = "include_sources"
CONF_EXCLUDE_SOURCES: Final = "exclude_sources"
CONF_INCLUDE_DOMAINS: Final = "include_domains"
CONF_EXCLUDE_DOMAINS: Final = "exclude_domains"
CONF_INCLUDE_DISCOVERY_DATA: Final = "include_discovery_data"
CONF_DEBUG_LOGGING: Final = "debug_logging"

DEFAULT_OPTIONS: Final = {
    CONF_ENABLED: True,
    CONF_INCLUDE_SOURCES: "",
    CONF_EXCLUDE_SOURCES: "",
    CONF_INCLUDE_DOMAINS: "",
    CONF_EXCLUDE_DOMAINS: "",
    CONF_INCLUDE_DISCOVERY_DATA: True,
    CONF_DEBUG_LOGGING: False,
}
