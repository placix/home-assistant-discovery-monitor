from __future__ import annotations

import asyncio
from typing import Any

import pytest

from custom_components.discovery_monitor.identity import DEVICE
from custom_components.discovery_monitor.storage import DiscoveryStore


class FakeStore:
    def __init__(self) -> None:
        self.saved: dict[str, Any] | None = None

    async def async_save(self, data: dict[str, Any]) -> None:
        self.saved = data


def make_store() -> DiscoveryStore:
    storage = object.__new__(DiscoveryStore)
    storage._store = FakeStore()
    storage._lock = asyncio.Lock()
    storage.rules = []
    storage.findings = {}
    return storage


@pytest.mark.asyncio
async def test_findings_are_updated_instead_of_duplicated() -> None:
    storage = make_store()
    device = {"domain": "example", "unique_id": "device-1"}

    first_id = await storage.async_record(
        domain="example",
        source="dhcp",
        label="Example device",
        device=device,
        device_type=None,
        fallback={},
    )
    second_id = await storage.async_record(
        domain="example",
        source="dhcp",
        label="Example device",
        device=device,
        device_type=None,
        fallback={},
    )

    assert first_id == second_id
    assert len(storage.findings) == 1
    assert storage.findings[first_id]["count"] == 2


@pytest.mark.asyncio
async def test_rule_has_only_required_fields_and_removal_keeps_history() -> None:
    storage = make_store()
    finding_id = await storage.async_record(
        domain="example",
        source="dhcp",
        label="Example device",
        device={"domain": "example", "unique_id": "device-1"},
        device_type=None,
        fallback={},
    )

    assert await storage.async_add_rule(finding_id, DEVICE)
    assert set(storage.rules[0]) == {"kind", "criteria", "label", "created_at"}
    assert storage.matching_rule(
        {"domain": "example", "unique_id": "device-1"}, None
    )

    rule_id = storage.rule_id(storage.rules[0])
    assert await storage.async_remove_rule(rule_id)
    assert not storage.rules
    assert finding_id in storage.findings
