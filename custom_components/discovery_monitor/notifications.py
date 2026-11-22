"""Bounded, persistent notification pauses, independent of ignore rules."""

from __future__ import annotations

import hashlib
import json
import math
import time
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.helpers.storage import Store

from .const import DOMAIN

if TYPE_CHECKING:
    from collections.abc import Mapping

MAX_PAUSE_HOURS = 720
MAX_NOTIFICATION_IDENTITIES = 1000
_RETENTION_SECONDS = MAX_PAUSE_HOURS * 3600
_MAC_LENGTH = 12
_KEY_LENGTH = 64


def notification_key(
    domain: str, source: str, device: Mapping[str, Any] | None, data: Mapping[str, Any]
) -> str | None:
    """Use exact HA identities, never link different Bluetooth addresses."""
    if not device:
        return None
    identity = dict(device)
    if source == "bluetooth":
        # Only a MAC-valued HA unique ID is supported initially. Do not promote
        # the legacy hardware fallback's MAC-bit heuristic to a stable identity.
        unique_id = device.get("unique_id")
        address = data.get("address", unique_id)
        if not isinstance(unique_id, str) or not isinstance(address, str):
            return None
        uid = unique_id.replace(":", "").replace("-", "").lower()
        address = address.replace(":", "").replace("-", "").lower()
        if (
            len(uid) != _MAC_LENGTH
            or any(char not in "0123456789abcdef" for char in uid)
            or uid != address
            or uid in {"0" * _MAC_LENGTH, "f" * _MAC_LENGTH}
            or data.get("address_type")
            not in (None, "public", "public_identity", "random_static", "static")
        ):
            return None
        # Unknown address type does not prove physical permanence: this key
        # refers only to the same HA discovery address within a bounded window.
        identity = {"domain": domain, "address": address}
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


class NotificationHistory:
    """Remember actual event emission times without depending on finding eviction."""

    def __init__(self, hass: Any) -> None:
        """Initialize a separate HA Store; existing rule storage is untouched."""
        self._store: Store[dict[str, float]] = Store(
            hass, 1, f"{DOMAIN}.notifications", atomic_writes=True
        )
        self.last_emitted: dict[str, float] = {}

    async def async_load(self) -> None:
        """Load valid timestamps and bound memory usage."""
        data = await self._store.async_load()
        if isinstance(data, dict):
            self.last_emitted = {
                key: float(value)
                for key, value in data.items()
                if isinstance(key, str)
                and len(key) == _KEY_LENGTH
                and all(char in "0123456789abcdef" for char in key)
                and type(value) in (int, float)
                and math.isfinite(value)
            }
        self._prune()

    @callback
    def is_paused(self, key: str | None, hours: int) -> bool:
        """Never extend a pause merely because another advertisement arrived."""
        if not key or hours <= 0:
            return False
        self._prune()
        previous = self.last_emitted.get(key)
        return previous is not None and 0 <= time.time() - previous < hours * 3600

    @callback
    def async_event_emitted(self, key: str) -> None:
        """Record only after an event was actually submitted to the HA event bus."""
        self.last_emitted[key] = time.time()
        self._prune()
        self._store.async_delay_save(lambda: dict(self.last_emitted), 1)

    async def async_reset(self) -> None:
        """Explicitly clear pauses without touching discoveries or ignore rules."""
        self.last_emitted.clear()
        await self._store.async_save({})

    def _prune(self) -> None:
        """Expire old timestamps and cap retained discovery identities."""
        now = time.time()
        self.last_emitted = dict(
            sorted(
                (
                    (key, value)
                    for key, value in self.last_emitted.items()
                    if 0 <= now - value <= _RETENTION_SECONDS
                ),
                key=lambda item: item[1],
                reverse=True,
            )[:MAX_NOTIFICATION_IDENTITIES]
        )
