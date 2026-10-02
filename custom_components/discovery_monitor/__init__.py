"""Discovery Monitor integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import callback

from .const import (
    CONF_DEBUG_LOGGING,
    CONF_ENABLED,
    CONF_EXCLUDE_DOMAINS,
    CONF_EXCLUDE_SOURCES,
    CONF_INCLUDE_DISCOVERY_DATA,
    CONF_INCLUDE_DOMAINS,
    CONF_INCLUDE_SOURCES,
    DEFAULT_OPTIONS,
    DOMAIN,
)
from .monitor import DiscoveryMonitor
from .storage import DiscoveryStore

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import Event, HomeAssistant
    from homeassistant.helpers.typing import ConfigType

CONFIG_SCHEMA = vol.Schema(
    {
        vol.Optional(DOMAIN): vol.Schema(
            {
                vol.Optional(
                    CONF_ENABLED, default=DEFAULT_OPTIONS[CONF_ENABLED]
                ): cv.boolean,
                vol.Optional(
                    CONF_INCLUDE_DISCOVERY_DATA,
                    default=DEFAULT_OPTIONS[CONF_INCLUDE_DISCOVERY_DATA],
                ): cv.boolean,
                vol.Optional(
                    CONF_DEBUG_LOGGING,
                    default=DEFAULT_OPTIONS[CONF_DEBUG_LOGGING],
                ): cv.boolean,
                vol.Optional(
                    CONF_INCLUDE_SOURCES,
                    default=DEFAULT_OPTIONS[CONF_INCLUDE_SOURCES],
                ): cv.string,
                vol.Optional(
                    CONF_EXCLUDE_SOURCES,
                    default=DEFAULT_OPTIONS[CONF_EXCLUDE_SOURCES],
                ): cv.string,
                vol.Optional(
                    CONF_INCLUDE_DOMAINS,
                    default=DEFAULT_OPTIONS[CONF_INCLUDE_DOMAINS],
                ): cv.string,
                vol.Optional(
                    CONF_EXCLUDE_DOMAINS,
                    default=DEFAULT_OPTIONS[CONF_EXCLUDE_DOMAINS],
                ): cv.string,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Discovery Monitor from YAML, when configured there."""
    if DOMAIN not in config:
        return True

    await _async_start_monitor(hass, dict(config[DOMAIN]))

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the UI entry without creating entities."""
    if DOMAIN not in hass.data:
        monitor = await _async_start_monitor(
            hass, DEFAULT_OPTIONS | dict(entry.options)
        )
        monitor.config_entry_id = entry.entry_id
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a UI-owned monitor while leaving YAML setup active."""
    monitor = hass.data.get(DOMAIN)
    if getattr(monitor, "config_entry_id", None) == entry.entry_id:
        monitor.async_stop()
        hass.data.pop(DOMAIN, None)
    return True


async def _async_start_monitor(
    hass: HomeAssistant, options: dict[str, Any]
) -> DiscoveryMonitor:
    """Load storage before subscribing so no matching flow can slip through."""
    storage = None
    # HomeAssistant always has ``config``. Keeping the lightweight fallback makes
    # the monitor usable by compatibility/test hosts without filesystem storage.
    if hasattr(hass, "config"):
        storage = DiscoveryStore(hass)
        await storage.async_load()
    monitor = DiscoveryMonitor(hass, options, storage)
    hass.data[DOMAIN] = monitor
    monitor.async_start()

    @callback
    def async_stop_monitor(_event: Event) -> None:
        monitor.async_stop()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, async_stop_monitor)
    return monitor
