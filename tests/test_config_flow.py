from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.data_entry_flow import FlowResultType

from custom_components.discovery_monitor.config_flow import DiscoveryOptionsFlow
from custom_components.discovery_monitor.const import DOMAIN
from custom_components.discovery_monitor.identity import DEVICE


class FakeStorage:
    def __init__(self) -> None:
        self.findings = {
            "private-fingerprint": {
                "label": "Kitchen Shelly",
                "domain": "shelly",
                "source": "zeroconf",
                "count": 3,
                "last_seen": "2026-10-03T08:00:00+00:00",
                "device_criteria": {
                    "domain": "shelly",
                    "unique_id": "technical-device-id",
                },
                "type_criteria": None,
            }
        }
        self.rules = [
            {
                "kind": DEVICE,
                "criteria": {"domain": "shelly", "unique_id": "device-1"},
                "label": "Kitchen Shelly",
                "created_at": "2026-10-03T08:00:00+00:00",
            }
        ]
        self.removed: list[str] = []

    @staticmethod
    def rule_id(_rule: dict[str, Any]) -> str:
        return "private-rule-id"

    async def async_remove_rule(self, rule_id: str) -> bool:
        self.removed.append(rule_id)
        return True


def make_flow() -> tuple[DiscoveryOptionsFlow, Any, FakeStorage]:
    storage = FakeStorage()
    monitor = SimpleNamespace(
        storage=storage,
        async_ignore_finding=AsyncMock(return_value=True),
    )
    entry = SimpleNamespace(options={})
    flow = DiscoveryOptionsFlow()
    flow.hass = SimpleNamespace(
        data={DOMAIN: monitor},
        config_entries=SimpleNamespace(async_get_known_entry=lambda _entry_id: entry),
    )
    flow.handler = "entry-id"
    return flow, monitor, storage


@pytest.mark.asyncio
async def test_options_start_page_is_management_menu() -> None:
    flow, _, _ = make_flow()

    result = await flow.async_step_init()

    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["recent_findings", "ignored_items"]


@pytest.mark.asyncio
async def test_recent_finding_can_be_ignored_without_showing_ids() -> None:
    flow, monitor, _ = make_flow()

    selection = await flow.async_step_recent_findings()
    validator = next(iter(selection["data_schema"].schema.values()))
    labels = " ".join(validator.container.values())

    assert selection["type"] is FlowResultType.FORM
    assert "private-fingerprint" not in labels
    assert "technical-device-id" not in labels

    action = await flow.async_step_recent_findings({"finding": "private-fingerprint"})
    assert action["type"] is FlowResultType.FORM
    assert action["step_id"] == "finding_action"

    completed = await flow.async_step_finding_action({"action": DEVICE})
    assert completed["type"] is FlowResultType.CREATE_ENTRY
    monitor.async_ignore_finding.assert_awaited_once_with("private-fingerprint", DEVICE)


@pytest.mark.asyncio
async def test_ignore_rule_can_be_removed_from_subpage() -> None:
    flow, _, storage = make_flow()

    form = await flow.async_step_ignored_items()
    completed = await flow.async_step_ignored_items({"ignored_item": "private-rule-id"})

    assert form["type"] is FlowResultType.FORM
    assert form["step_id"] == "ignored_items"
    assert completed["type"] is FlowResultType.CREATE_ENTRY
    assert storage.removed == ["private-rule-id"]
