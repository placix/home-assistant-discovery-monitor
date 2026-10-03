from __future__ import annotations

import asyncio
from typing import Any

import pytest

from custom_components.discovery_monitor.identity import DEVICE
from custom_components.discovery_monitor.storage import MAX_FINDINGS, DiscoveryStore


class FakeStore:
    def __init__(self) -> None:
        self.saved: dict[str, Any] | None = None
        self.save_calls = 0
        self.delay_calls = 0
        self.delayed_data: Any = None

    async def async_save(self, data: dict[str, Any]) -> None:
        self.save_calls += 1
        self.saved = data

    def async_delay_save(self, data_func: Any, _delay: float) -> None:
        self.delay_calls += 1
        self.delayed_data = data_func


def make_store() -> DiscoveryStore:
    storage = object.__new__(DiscoveryStore)
    storage._store = FakeStore()
    storage._lock = asyncio.Lock()
    storage.rules = []
    storage.findings = {}
    storage._active_flow_ids = {}
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
        flow_id="flow-1",
    )
    second_id = await storage.async_record(
        domain="example",
        source="dhcp",
        label="Example device",
        device=device,
        device_type=None,
        fallback={},
        flow_id="flow-2",
    )

    assert first_id == second_id
    assert len(storage.findings) == 1
    assert storage.findings[first_id]["count"] == 2
    assert storage.active_flow_id(first_id) == "flow-2"


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
        flow_id="flow-1",
    )

    assert await storage.async_add_rule(finding_id, DEVICE)
    assert set(storage.rules[0]) == {"kind", "criteria", "label", "created_at"}
    assert storage.matching_rule({"domain": "example", "unique_id": "device-1"}, None)

    rule_id = storage.rule_id(storage.rules[0])
    assert await storage.async_remove_rule(rule_id)
    assert not storage.rules
    assert finding_id in storage.findings


@pytest.mark.asyncio
async def test_findings_are_available_without_waiting_for_disk_save() -> None:
    storage = make_store()

    finding_id = await storage.async_record(
        domain="example",
        source="dhcp",
        label="Immediate device",
        device={"domain": "example", "unique_id": "immediate"},
        device_type=None,
        fallback={},
        flow_id="flow-immediate",
    )

    assert finding_id in storage.findings
    assert storage._store.save_calls == 0
    assert storage._store.delayed_data()["findings"][finding_id]["count"] == 1


@pytest.mark.asyncio
async def test_only_latest_one_hundred_distinct_findings_are_kept() -> None:
    storage = make_store()
    finding_ids = [
        await storage.async_record(
            domain="example",
            source="dhcp",
            label=f"Device {number}",
            device={"domain": "example", "unique_id": f"device-{number}"},
            device_type=None,
            fallback={},
            flow_id=f"flow-{number}",
        )
        for number in range(MAX_FINDINGS + 1)
    ]

    assert len(storage.findings) == MAX_FINDINGS
    assert finding_ids[0] not in storage.findings
    assert finding_ids[-1] in storage.findings


@pytest.mark.asyncio
async def test_rapid_findings_use_delayed_storage_instead_of_individual_saves() -> None:
    storage = make_store()

    for number in range(3):
        await storage.async_record(
            domain="example",
            source="dhcp",
            label=f"Device {number}",
            device={"domain": "example", "unique_id": f"device-{number}"},
            device_type=None,
            fallback={},
            flow_id=f"flow-{number}",
        )

    assert storage._store.save_calls == 0
    assert storage._store.delay_calls == 3
    assert len(storage._store.delayed_data()["findings"]) == 3
