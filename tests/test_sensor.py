from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.discovery_monitor.const import DOMAIN
from custom_components.discovery_monitor.sensor import DiscoverySummarySensor
from custom_components.discovery_monitor.storage import DiscoveryStore


class FakeSummaryStore:
    def __init__(self, findings: list[tuple[str, dict[str, Any]]]) -> None:
        self.findings = findings
        self.listener = None
        self.unsubscribed = False

    def visible_findings(self) -> list[tuple[str, dict[str, Any]]]:
        return self.findings

    def async_subscribe(self, listener: Any) -> Any:
        self.listener = listener

        def unsubscribe() -> None:
            self.unsubscribed = True
            self.listener = None

        return unsubscribe


def test_sensor_counts_distinct_findings_and_exposes_safe_recent_list() -> None:
    findings = [
        (
            f"technical-id-{number}",
            {
                "label": f"Device {number}",
                "domain": "example",
                "source": "dhcp",
                "count": number + 1,
                "last_seen": f"2026-10-18T10:0{number}:00+00:00",
                "device_criteria": {"unique_id": f"secret-{number}"},
            },
        )
        for number in range(6, -1, -1)
    ]
    sensor = DiscoverySummarySensor(FakeSummaryStore(findings))  # type: ignore[arg-type]

    assert sensor.native_value == 7
    recent = sensor.extra_state_attributes["recent_discoveries"]
    assert len(recent) == 5
    assert recent[0]["name"] == "Device 6"
    assert set(recent[0]) == {
        "name",
        "integration",
        "source",
        "count",
        "last_seen",
    }
    assert "technical-id" not in json.dumps(recent)
    assert "secret" not in json.dumps(recent)


def test_repeated_finding_changes_summary_but_not_state() -> None:
    store = FakeSummaryStore(
        [
            (
                "id",
                {
                    "label": "Device",
                    "domain": "example",
                    "source": "dhcp",
                    "count": 1,
                    "last_seen": "first",
                },
            )
        ]
    )
    sensor = DiscoverySummarySensor(store)  # type: ignore[arg-type]

    assert sensor.native_value == 1
    store.findings[0][1]["count"] = 2
    store.findings[0][1]["last_seen"] = "second"

    assert sensor.native_value == 1
    assert sensor.extra_state_attributes["recent_discoveries"][0]["count"] == 2
    assert (
        sensor.extra_state_attributes["recent_discoveries"][0]["last_seen"] == "second"
    )


@pytest.mark.asyncio
async def test_ui_setup_registers_one_sensor_without_a_device(
    hass: Any,
    enable_custom_integrations: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def async_load(_self: DiscoveryStore) -> None:
        return None

    monkeypatch.setattr(DiscoveryStore, "async_load", async_load)
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    entities = [
        entity
        for entity in entity_registry.entities.values()
        if entity.platform == DOMAIN
    ]
    assert len(entities) == 1
    assert entities[0].entity_id == "sensor.discovery_monitor_open_discoveries"
    assert entities[0].unique_id == "discovery_monitor_open_discoveries"
    assert hass.states.get(entities[0].entity_id).state == "0"
    assert not dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)

    monitor = hass.data[DOMAIN]
    assert len(monitor.storage._listeners) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert not monitor.storage._listeners


def test_sensor_name_translations_and_english_fallback() -> None:
    root = Path(__file__).parents[1] / "custom_components" / DOMAIN
    strings = json.loads((root / "strings.json").read_text(encoding="utf-8"))
    english = json.loads(
        (root / "translations" / "en.json").read_text(encoding="utf-8")
    )
    german = json.loads((root / "translations" / "de.json").read_text(encoding="utf-8"))

    assert strings["entity"]["sensor"]["open_discoveries"]["name"] == (
        "Open discoveries"
    )
    assert english["entity"]["sensor"]["open_discoveries"]["name"] == (
        "Open discoveries"
    )
    assert german["entity"]["sensor"]["open_discoveries"]["name"] == "Offene Funde"
