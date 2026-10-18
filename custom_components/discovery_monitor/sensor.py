"""Discovery summary sensor for Discovery Monitor."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .storage import DiscoveryStore

_ATTR_RECENT_DISCOVERIES = "recent_discoveries"
_MAX_RECENT_DISCOVERIES = 5


async def async_setup_entry(
    hass: HomeAssistant,
    _entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the discovery summary sensor."""
    monitor = hass.data[DOMAIN]
    storage: DiscoveryStore = monitor.storage
    async_add_entities([DiscoverySummarySensor(storage)])


class DiscoverySummarySensor(SensorEntity):
    """Summarize discoveries that are currently visible to the user."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_icon = "mdi:radar"
    _attr_should_poll = False
    _attr_translation_key = "open_discoveries"
    _attr_unique_id = f"{DOMAIN}_open_discoveries"
    _unrecorded_attributes = frozenset({_ATTR_RECENT_DISCOVERIES})

    def __init__(self, storage: DiscoveryStore) -> None:
        """Initialize the sensor."""
        self._storage = storage
        self.entity_id = f"sensor.{DOMAIN}_open_discoveries"

    @property
    def native_value(self) -> int:
        """Return the number of distinct visible discoveries."""
        return len(self._storage.visible_findings())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return a small safe summary of recent visible discoveries."""
        recent = []
        for _finding_id, finding in self._storage.visible_findings()[
            :_MAX_RECENT_DISCOVERIES
        ]:
            recent.append(
                {
                    "name": str(
                        finding.get("label") or finding.get("domain") or "Discovery"
                    ),
                    "integration": str(finding.get("domain") or "unknown"),
                    "source": str(finding.get("source") or "unknown"),
                    "count": max(int(finding.get("count", 1)), 1),
                    "last_seen": str(finding.get("last_seen") or ""),
                }
            )
        return {_ATTR_RECENT_DISCOVERIES: recent}

    async def async_added_to_hass(self) -> None:
        """Subscribe to in-memory discovery summary changes."""
        await super().async_added_to_hass()
        self.async_on_remove(self._storage.async_subscribe(self._handle_store_update))

    def _handle_store_update(self) -> None:
        """Publish an updated state after a storage change."""
        self.async_write_ha_state()
