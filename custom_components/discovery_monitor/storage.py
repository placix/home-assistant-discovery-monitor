"""Persistent rules and discovery history."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .identity import DEVICE, DEVICE_TYPE, criteria_match

_STORAGE_KEY = f"{DOMAIN}.data"
_STORAGE_VERSION = 1


class DiscoveryStore:
    """Keep normalized ignore rules and a de-duplicated discovery log."""

    def __init__(self, hass: Any) -> None:
        """Initialize storage."""
        self._store: Store[dict[str, Any]] = Store(
            hass, _STORAGE_VERSION, _STORAGE_KEY, atomic_writes=True
        )
        self._lock = asyncio.Lock()
        self.rules: list[dict[str, Any]] = []
        self.findings: dict[str, dict[str, Any]] = {}

    async def async_load(self) -> None:
        """Load persisted state."""
        data = await self._store.async_load() or {}
        rules = data.get("rules", [])
        findings = data.get("findings", {})
        if isinstance(rules, list):
            self.rules = [rule for rule in rules if _valid_rule(rule)]
        if isinstance(findings, Mapping):
            self.findings = {
                str(key): dict(value)
                for key, value in findings.items()
                if isinstance(value, Mapping)
            }

    def matching_rule(
        self,
        device: Mapping[str, Any] | None,
        device_type: Mapping[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Return the first matching ignore rule."""
        for rule in self.rules:
            candidate = device if rule["kind"] == DEVICE else device_type
            if criteria_match(rule["criteria"], candidate):
                return rule
        return None

    async def async_record(  # noqa: PLR0913
        self,
        *,
        domain: str,
        source: str,
        label: str,
        device: Mapping[str, Any] | None,
        device_type: Mapping[str, Any] | None,
        fallback: Mapping[str, Any],
    ) -> str:
        """Insert a finding or update its last occurrence and count."""
        async with self._lock:
            signature: Mapping[str, Any] = device or {
                "domain": domain,
                "source": source,
                "type": device_type,
                "label": label.strip().lower(),
                "data": fallback,
            }
            finding_id = _digest(signature)
            now = datetime.now(UTC).isoformat()
            if finding := self.findings.get(finding_id):
                finding["last_seen"] = now
                finding["count"] = int(finding.get("count", 1)) + 1
                finding["label"] = label
                if device is not None:
                    finding["device_criteria"] = dict(device)
                if device_type is not None:
                    finding["type_criteria"] = dict(device_type)
            else:
                self.findings[finding_id] = {
                    "id": finding_id,
                    "domain": domain,
                    "source": source,
                    "label": label,
                    "first_seen": now,
                    "last_seen": now,
                    "count": 1,
                    "device_criteria": dict(device) if device else None,
                    "type_criteria": dict(device_type) if device_type else None,
                }
            await self._async_save()
        return finding_id

    async def async_add_rule(self, finding_id: str, kind: str) -> bool:
        """Create a rule from a logged finding if that identity is safe."""
        async with self._lock:
            finding = self.findings.get(finding_id)
            field = "device_criteria" if kind == DEVICE else "type_criteria"
            if finding is None or not isinstance(
                criteria := finding.get(field), Mapping
            ):
                return False
            rule = {
                "kind": kind,
                "criteria": dict(criteria),
                "label": str(
                    finding.get("label") or finding.get("domain") or "Gerät"
                ),
                "created_at": datetime.now(UTC).isoformat(),
            }
            if not any(
                existing["kind"] == kind
                and criteria_match(existing["criteria"], criteria)
                for existing in self.rules
            ):
                self.rules.append(rule)
                await self._async_save()
        return True

    async def async_remove_rule(self, rule_id: str) -> bool:
        """Remove the selected rule without touching history."""
        async with self._lock:
            old_length = len(self.rules)
            self.rules = [
                rule for rule in self.rules if self.rule_id(rule) != rule_id
            ]
            if len(self.rules) == old_length:
                return False
            await self._async_save()
        return True

    @staticmethod
    def rule_id(rule: Mapping[str, Any]) -> str:
        """Derive an identifier so rules need no extra stored field."""
        return _digest({"kind": rule.get("kind"), "criteria": rule.get("criteria")})

    async def _async_save(self) -> None:
        """Persist all state using Home Assistant storage."""
        await self._store.async_save({"rules": self.rules, "findings": self.findings})


def _digest(value: Mapping[str, Any]) -> str:
    serialized = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(serialized.encode()).hexdigest()[:24]


def _valid_rule(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and value.get("kind") in {DEVICE, DEVICE_TYPE}
        and isinstance(value.get("criteria"), Mapping)
        and isinstance(value.get("label"), str)
        and isinstance(value.get("created_at"), str)
        and set(value) == {"kind", "criteria", "label", "created_at"}
    )
