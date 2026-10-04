from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.selector import SelectSelector

from custom_components.discovery_monitor import config_flow as config_flow_module
from custom_components.discovery_monitor.config_flow import DiscoveryOptionsFlow
from custom_components.discovery_monitor.const import DOMAIN
from custom_components.discovery_monitor.identity import (
    DEVICE,
    DEVICE_TYPE,
    criteria_match,
)


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
                "type_criteria": {
                    "domain": "shelly",
                    "model": "shelly-plus",
                },
            }
        }
        self.rules: list[dict[str, Any]] = []
        self.removed: list[str] = []

    @staticmethod
    def rule_id(rule: dict[str, Any]) -> str:
        return f"private-rule-{rule['kind']}"

    def matching_rule(
        self,
        device: dict[str, Any] | None,
        device_type: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        for rule in self.rules:
            candidate = device if rule["kind"] == DEVICE else device_type
            if criteria_match(rule["criteria"], candidate):
                return rule
        return None

    async def async_remove_rule(self, rule_id: str) -> bool:
        self.removed.append(rule_id)
        old_length = len(self.rules)
        self.rules = [rule for rule in self.rules if self.rule_id(rule) != rule_id]
        return len(self.rules) != old_length

    def add_matching_rule(self, kind: str) -> str:
        field = "device_criteria" if kind == DEVICE else "type_criteria"
        rule = {
            "kind": kind,
            "criteria": dict(self.findings["private-fingerprint"][field]),
            "label": "Kitchen Shelly",
            "created_at": "2026-10-03T08:00:00+00:00",
        }
        self.rules.append(rule)
        return self.rule_id(rule)


@pytest.fixture(autouse=True)
def use_component_translation_files(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the integration's real translations in isolated flow tests."""

    async def async_get_test_translations(
        _hass: Any,
        language: str,
        category: str,
        integrations: set[str],
    ) -> dict[str, str]:
        translation_path = (
            Path(__file__).parents[1]
            / "custom_components"
            / DOMAIN
            / "translations"
            / f"{language}.json"
        )
        common = json.loads(translation_path.read_text(encoding="utf-8"))[category]
        return {
            f"component.{DOMAIN}.{category}.{key}": value
            for key, value in common.items()
        }

    monkeypatch.setattr(
        config_flow_module,
        "async_get_translations",
        async_get_test_translations,
    )


def make_flow(language: str = "en") -> tuple[DiscoveryOptionsFlow, Any, FakeStorage]:
    storage = FakeStorage()

    async def async_ignore_finding(finding_id: str, kind: str) -> bool:
        if finding_id not in storage.findings:
            return False
        storage.add_matching_rule(kind)
        return True

    monitor = SimpleNamespace(
        storage=storage,
        async_ignore_finding=AsyncMock(side_effect=async_ignore_finding),
    )
    entry = SimpleNamespace(options={})
    flow = DiscoveryOptionsFlow()
    flow.hass = SimpleNamespace(
        config=SimpleNamespace(language=language),
        data={DOMAIN: monitor},
        config_entries=SimpleNamespace(async_get_known_entry=lambda _entry_id: entry),
    )
    flow.handler = "entry-id"
    return flow, monitor, storage


def selector_labels(form: dict[str, Any]) -> str:
    selector = next(iter(form["data_schema"].schema.values()))
    return " ".join(option["label"] for option in selector.config["options"])


def matching_rule_labels(action_form: dict[str, Any]) -> str:
    validator = next(iter(action_form["data_schema"].schema.values()))
    return " ".join(validator.container.values())


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
    selector = next(iter(selection["data_schema"].schema.values()))
    labels = " ".join(option["label"] for option in selector.config["options"])

    assert selection["type"] is FlowResultType.FORM
    assert isinstance(selector, SelectSelector)
    assert selector.config["mode"] == "list"
    assert "Kitchen Shelly" in labels
    assert "Integration: shelly" in labels
    assert "Source: zeroconf" in labels
    assert "Discoveries: 3" in labels
    assert "Last seen: 2026-10-03T08:00:00+00:00" in labels
    assert "private-fingerprint" not in labels
    assert "technical-device-id" not in labels

    action = await flow.async_step_recent_findings({"finding": "private-fingerprint"})
    assert action["type"] is FlowResultType.FORM
    assert action["step_id"] == "finding_action"

    returned = await flow.async_step_finding_action({"action": DEVICE})
    assert returned["type"] is FlowResultType.FORM
    assert returned["step_id"] == "recent_findings"
    assert returned["data_schema"].schema == {}
    monitor.async_ignore_finding.assert_awaited_once_with("private-fingerprint", DEVICE)


@pytest.mark.asyncio
async def test_ignore_rule_can_be_removed_from_subpage() -> None:
    flow, _, storage = make_flow()
    rule_id = storage.add_matching_rule(DEVICE)

    form = await flow.async_step_ignored_items()
    selector = next(iter(form["data_schema"].schema.values()))
    labels = " ".join(option["label"] for option in selector.config["options"])
    returned = await flow.async_step_ignored_items({"ignored_item": rule_id})

    assert form["type"] is FlowResultType.FORM
    assert form["step_id"] == "ignored_items"
    assert isinstance(selector, SelectSelector)
    assert selector.config["mode"] == "list"
    assert "Kitchen Shelly — Device · Integration: shelly" in labels
    assert rule_id not in labels
    assert "technical-device-id" not in labels
    assert returned["type"] is FlowResultType.FORM
    assert returned["step_id"] == "ignored_items"
    assert returned["data_schema"].schema == {}
    assert storage.removed == [rule_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["recent_findings", "ignored_items"])
async def test_empty_management_list_closes_when_confirmed(step: str) -> None:
    flow, _, storage = make_flow()
    storage.findings.clear()

    show_step = getattr(flow, f"async_step_{step}")
    empty = await show_step()
    completed = await show_step({})

    assert empty["type"] is FlowResultType.FORM
    assert empty["step_id"] == step
    assert empty["data_schema"].schema == {}
    assert completed["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [DEVICE, DEVICE_TYPE])
async def test_ignore_action_returns_to_updated_recent_findings(kind: str) -> None:
    flow, _, storage = make_flow()

    await flow.async_step_recent_findings({"finding": "private-fingerprint"})
    returned = await flow.async_step_finding_action({"action": kind})
    completed = await flow.async_step_recent_findings({})

    assert returned["type"] is FlowResultType.FORM
    assert returned["step_id"] == "recent_findings"
    assert returned["data_schema"].schema == {}
    assert "private-fingerprint" in storage.findings
    assert completed["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
async def test_remove_rule_returns_to_updated_ignored_items() -> None:
    flow, _, storage = make_flow()
    rule_id = storage.add_matching_rule(DEVICE)

    returned = await flow.async_step_ignored_items({"ignored_item": rule_id})
    completed = await flow.async_step_ignored_items({})

    assert returned["type"] is FlowResultType.FORM
    assert returned["step_id"] == "ignored_items"
    assert returned["data_schema"].schema == {}
    assert storage.rules == []
    assert completed["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [DEVICE, DEVICE_TYPE])
async def test_ignored_finding_is_hidden_without_deleting_history(kind: str) -> None:
    flow, _, storage = make_flow()
    storage.add_matching_rule(kind)

    form = await flow.async_step_recent_findings()

    assert form["data_schema"].schema == {}
    assert "private-fingerprint" in storage.findings


@pytest.mark.asyncio
async def test_finding_is_visible_again_after_rule_is_removed() -> None:
    flow, _, storage = make_flow()
    rule_id = storage.add_matching_rule(DEVICE_TYPE)

    hidden = await flow.async_step_recent_findings()
    await flow.async_step_ignored_items({"ignored_item": rule_id})
    visible = await flow.async_step_recent_findings()

    assert hidden["data_schema"].schema == {}
    assert "Kitchen Shelly" in selector_labels(visible)
    assert "private-fingerprint" in storage.findings


@pytest.mark.asyncio
async def test_german_dynamic_labels() -> None:
    flow, _, storage = make_flow("de")

    findings = await flow.async_step_recent_findings()
    action = await flow.async_step_recent_findings({"finding": "private-fingerprint"})
    storage.add_matching_rule(DEVICE_TYPE)
    ignored = await flow.async_step_ignored_items()

    assert "Integration: shelly" in selector_labels(findings)
    assert "Quelle: zeroconf" in selector_labels(findings)
    assert "Funde: 3" in selector_labels(findings)
    assert "Zuletzt gesehen:" in selector_labels(findings)
    assert "Dieses Gerät zukünftig ignorieren" in matching_rule_labels(action)
    assert "Diesen Gerätetyp zukünftig ignorieren" in matching_rule_labels(action)
    assert "Kitchen Shelly — Gerätetyp · Integration: shelly" in selector_labels(
        ignored
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["en", "fr"])
async def test_english_dynamic_labels_and_fallback(language: str) -> None:
    flow, _, storage = make_flow(language)

    findings = await flow.async_step_recent_findings()
    action = await flow.async_step_recent_findings({"finding": "private-fingerprint"})
    storage.add_matching_rule(DEVICE)
    ignored = await flow.async_step_ignored_items()

    assert "Integration: shelly" in selector_labels(findings)
    assert "Source: zeroconf" in selector_labels(findings)
    assert "Discoveries: 3" in selector_labels(findings)
    assert "Last seen:" in selector_labels(findings)
    assert "Ignore this device in the future" in matching_rule_labels(action)
    assert "Ignore this device type in the future" in matching_rule_labels(action)
    assert "Kitchen Shelly — Device · Integration: shelly" in selector_labels(ignored)
