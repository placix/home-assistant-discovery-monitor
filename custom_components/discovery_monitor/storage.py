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
_FINDING_SAVE_DELAY = 1.0
MAX_FINDINGS = 100


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
        self._active_flow_ids: dict[str, str] = {}

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
        self._trim_findings()

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
        flow_id: str,
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
            self._active_flow_ids[finding_id] = flow_id
            self._trim_findings()
            self._store.async_delay_save(self._data_to_save, _FINDING_SAVE_DELAY)
        return finding_id

    def active_flow_id(self, finding_id: str) -> str | None:
        """Return the most recent in-progress flow associated with a finding."""
        return self._active_flow_ids.get(finding_id)

    async def async_add_rule(self, finding_id: str, kind: str) -> bool:
        """Create a rule from a logged finding if that identity is safe."""
        async with self._lock:
            if kind not in {DEVICE, DEVICE_TYPE}:
                return False
            finding = self.findings.get(finding_id)
            field = "device_criteria" if kind == DEVICE else "type_criteria"
            if finding is None or not isinstance(
                criteria := finding.get(field), Mapping
            ):
                return False
            rule = {
                "kind": kind,
                "criteria": dict(criteria),
                "label": str(finding.get("label") or finding.get("domain") or "Gerät"),
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
            self.rules = [rule for rule in self.rules if self.rule_id(rule) != rule_id]
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
        await self._store.async_save(self._data_to_save())

    def _data_to_save(self) -> dict[str, Any]:
        """Return current persistent state for immediate or delayed writes."""
        return {"rules": self.rules, "findings": self.findings}

    def _trim_findings(self) -> None:
        """Keep only the most recently seen distinct findings."""
        if len(self.findings) <= MAX_FINDINGS:
            return
        oldest = sorted(
            self.findings,
            key=lambda finding_id: str(self.findings[finding_id].get("last_seen", "")),
        )[: len(self.findings) - MAX_FINDINGS]
        for finding_id in oldest:
            self.findings.pop(finding_id, None)
            self._active_flow_ids.pop(finding_id, None)


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
