from __future__ import annotations

from copy import deepcopy
from typing import Any
from unittest.mock import patch

import pytest

from custom_components.discovery_monitor.identity import device_criteria, type_criteria
from custom_components.discovery_monitor.storage import DiscoveryStore


@pytest.mark.parametrize("kind", ["device", "device_type"])
async def test_legacy_rules_load_without_migration_and_keep_exact_matching(
    hass: Any, hass_storage: dict[str, Any], kind: str
) -> None:
    """Literal pre-PR rule shapes, independent of the new rule creation path."""
    criteria = (
        {"domain": "victron_ble", "unique_id": "c0:11:22:33:44:55"}
        if kind == "device"
        else {
            "domain": "govee_ble",
            "manufacturer_id": "1",
            "service_uuids": ["service-a", "service-b"],
        }
    )
    rule = {
        "kind": kind,
        "criteria": criteria,
        "label": "Existing rule",
        "created_at": "2026-10-01T12:00:00+00:00",
    }
    original = deepcopy(rule)
    storage = DiscoveryStore(hass)
    await storage._store.async_save({"rules": [rule], "findings": {}})
    original_serialized = deepcopy(hass_storage["discovery_monitor.data"])
    rule_id = storage.rule_id(rule)
    loaded = DiscoveryStore(hass)
    with patch.object(
        loaded._store, "async_save", wraps=loaded._store.async_save
    ) as save:
        await loaded.async_load()
        save.assert_not_awaited()
    assert loaded.rules == [original]
    assert loaded.rule_id(loaded.rules[0]) == rule_id
    assert hass_storage["discovery_monitor.data"] == original_serialized

    device = device_criteria("victron_ble", "bluetooth", "C0:11:22:33:44:55", {})
    device_type = type_criteria(
        "govee_ble",
        "bluetooth",
        {
            "manufacturer_id": 1,
            "service_uuids": ["service-b", "service-a"],
            "rssi": -60,
        },
    )
    candidate = device if kind == "device" else device_type
    assert candidate == criteria
    assert loaded.matching_rule(device, device_type) == original
    for changed in (
        candidate | {"domain": "different-domain"},
        candidate | {"unexpected": "extra-criterion"},
        {"domain": criteria["domain"]},
    ):
        args = (changed, None) if kind == "device" else (None, changed)
        assert loaded.matching_rule(*args) is None
    assert (
        loaded.matching_rule(
            None,
            None,
            {
                "domain": criteria["domain"],
                "profile": "bluetooth_model",
                "version": 1,
                "model": "a053",
            },
        )
        is None
    )
    assert loaded.rules == [original]

    fid = await loaded.async_record(
        domain=criteria["domain"],
        source="bluetooth",
        label="Repeat",
        device=device if kind == "device" else None,
        device_type=device_type if kind == "device_type" else None,
        fallback={},
        flow_id="new-flow",
    )
    assert loaded.findings[fid]
    assert not loaded.visible_findings()
    assert loaded.rules == [original]
    assert await loaded.async_remove_rule(rule_id)
    assert len(loaded.visible_findings()) == 1
