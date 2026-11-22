from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.discovery_monitor.bluetooth_models import (
    MODEL,
    detect_model,
    model_label,
)
from custom_components.discovery_monitor.identity import DEVICE, DEVICE_TYPE

from .test_storage import make_store

# Shapes verified against upstream parser fixtures, without real device IDs.
GOVEE = {
    "name": "GVH5105_ABCD",
    "manufacturer_data": {1: bytes.fromhex("010103325e64")},
}
THERMOPRO = {
    "name": "TP357S (ABCD)",
    "manufacturer_data": {194: bytes.fromhex("32646e1000")},
}
VICTRON = {"manufacturer_data": {0x02E1: bytes.fromhex("100053a001000000") + bytes(16)}}


@pytest.mark.parametrize(
    ("domain", "info", "label"),
    [
        ("govee_ble", GOVEE, "Govee H5105"),
        ("thermopro", THERMOPRO, "ThermoPro TP357S"),
        ("victron_ble", VICTRON, "SmartSolar MPPT 75/15"),
    ],
)
def test_model_uses_raw_protocol_data_not_display_placeholders(
    domain: str, info: dict[str, Any], label: str
) -> None:
    detected = detect_model(domain, "bluetooth", SimpleNamespace(**info))
    assert detected.label == label
    assert detected.criteria == {
        "domain": domain,
        "profile": "bluetooth_model",
        "version": 1,
        "model": {"govee_ble": "h5105", "thermopro": "tp357s", "victron_ble": "a053"}[
            domain
        ],
    }
    changed = info | {
        "rssi": -99,
        "address": "C0:11:22:33:44:55",
        "service_uuids": ["optional"],
    }
    assert detect_model(domain, "bluetooth", changed).criteria == detected.criteria
    assert detect_model(domain, "dhcp", info).criteria is None


@pytest.mark.parametrize(
    ("domain", "info"),
    [
        ("govee_ble", {"name": "GVH5105_ABCD"}),
        ("govee_ble", GOVEE | {"name": "Kitchen H5105"}),
        ("govee_ble", GOVEE | {"name": "GVH5108_ABCD"}),
        ("govee_ble", GOVEE | {"manufacturer_data": {1: bytes(8)}}),
        ("govee_ble", GOVEE | {"manufacturer_data": {1: b"\x01\x01" + bytes(6)}}),
        ("govee_ble", GOVEE | {"manufacturer_data": {1: b"\x01\x01"}}),
        ("govee_ble", {"manufacturer_id": 1, "service_uuids": ["ec88"]}),
        ("thermopro", THERMOPRO | {"name": "My TP357S"}),
        ("thermopro", THERMOPRO | {"name": "TP357 (ABCD)"}),
        ("thermopro", THERMOPRO | {"manufacturer_data": {194: bytes(4)}}),
        ("thermopro", THERMOPRO | {"manufacturer_data": {"194": bytes(5)}}),
        ("thermopro", {"name": "TP357S", "manufacturer_data": {}}),
        (
            "victron_ble",
            {"manufacturer_data": {0x02E1: bytes.fromhex("1000ffff01") + bytes(20)}},
        ),
        (
            "victron_ble",
            {"manufacturer_data": {0x02E1: bytes.fromhex("100053a002") + bytes(20)}},
        ),
        ("victron_ble", {"manufacturer_data": {0x02E1: bytes.fromhex("100053a001")}}),
        ("victron_ble", {"manufacturer_data": {0x02E1: bytes(24)}}),
        ("unknown", GOVEE),
        ("thermopro", None),
        ("govee_ble", {"manufacturer_data": []}),
    ],
)
def test_incomplete_or_ambiguous_data_never_offers_model_rule(
    domain: str, info: Any
) -> None:
    result = detect_model(domain, "bluetooth", info)
    assert result.criteria is None
    assert result.label is None


def test_govee_extended_frame_and_thermopro_changing_measurements() -> None:
    extended = (
        bytes.fromhex("0101033aed644c000215")
        + b"INTELLI_ROCKS_HWP"
        + bytes.fromhex("75f2ff0c")
    )
    assert (
        detect_model(
            "govee_ble", "bluetooth", GOVEE | {"manufacturer_data": {1: extended}}
        ).label
        == "Govee H5105"
    )
    assert (
        detect_model(
            "thermopro",
            "bluetooth",
            THERMOPRO | {"manufacturer_data": {65535: bytes(5)}},
        ).label
        == "ThermoPro TP357S"
    )


async def test_model_rules_are_explicit_versioned_persisted_and_independent() -> None:
    storage = make_store()
    model = detect_model("govee_ble", "bluetooth", GOVEE).criteria
    device = {"domain": "govee_ble", "unique_id": "c0:11:22:33:44:55"}
    device_type = {"domain": "govee_ble", "model": "legacy"}
    fid = await storage.async_record(
        domain="govee_ble",
        source="bluetooth",
        label="Room",
        device=device,
        device_type=device_type,
        model=model,
        fallback={},
        flow_id="one",
    )
    assert not storage.rules
    assert await storage.async_add_rule(fid, DEVICE)
    assert await storage.async_add_rule(fid, DEVICE_TYPE)
    old_rules = [dict(rule) for rule in storage.rules]
    assert await storage.async_add_rule(fid, MODEL)
    assert storage.rules[:2] == old_rules
    assert storage.rules[-1]["label"] == "Govee H5105"
    assert storage.rules[-1]["criteria"] == model
    reloaded = make_store()
    reloaded._store.async_load = AsyncMock(return_value=storage._store.saved)
    await reloaded.async_load()
    assert reloaded.rules == storage.rules
    assert reloaded.matching_rule(device, None) == old_rules[0]
    assert reloaded.matching_rule(None, device_type) == old_rules[1]
    assert (
        reloaded.matching_rule(
            {"domain": "govee_ble", "unique_id": "different-address"}, None, model
        )["kind"]
        == MODEL
    )
    assert reloaded.matching_rule(None, None, model | {"domain": "other"}) is None
    assert reloaded.matching_rule(None, None, model | {"version": 2}) is None
    for rule in list(reloaded.rules):
        assert await reloaded.async_remove_rule(reloaded.rule_id(rule))
    assert len(reloaded.visible_findings()) == 1


async def test_model_action_unavailable_for_legacy_finding_and_invalid_version() -> (
    None
):
    storage = make_store()
    storage.findings["old"] = {"domain": "thermopro", "label": "TP357S"}
    assert not await storage.async_add_rule("old", MODEL)
    model = detect_model("thermopro", "bluetooth", THERMOPRO).criteria
    for invalid in (
        model | {"version": 2},
        model | {"model": "all"},
        model | {"extra": "ignored"},
    ):
        assert model_label(invalid) is None
        storage.findings["old"]["model_criteria"] = invalid
        assert not await storage.async_add_rule("old", MODEL)
    assert not storage.rules
