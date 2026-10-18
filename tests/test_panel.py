from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components import frontend
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.discovery_monitor.const import DOMAIN
from custom_components.discovery_monitor.panel import (
    PANEL_PATH,
    async_setup_panel,
)


@pytest.fixture
async def panel_entry(hass: Any, enable_custom_integrations: Any) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def record(hass: Any, *, safe: bool = True) -> str:
    return await hass.data[DOMAIN].storage.async_record(
        domain="example",
        source="dhcp",
        label="Living room",
        device={"domain": "example", "unique_id": "private-id"} if safe else None,
        device_type={"domain": "example", "model": "Model"} if safe else None,
        fallback={"name": "Living room"},
        flow_id="private-flow",
    )


async def subscribe(client: Any, language: str = "en") -> dict[str, Any]:
    await client.send_json_auto_id(
        {"type": f"{DOMAIN}/subscribe", "language": language}
    )
    assert (await client.receive_json())["success"]
    return (await client.receive_json())["event"]


async def action(client: Any, operation: str, target: str) -> dict[str, Any]:
    await client.send_json_auto_id(
        {"type": f"{DOMAIN}/action", "action": operation, "target": target}
    )
    return await client.receive_json()


async def test_panel_registration_reload_and_static_module(
    hass: Any, panel_entry: Any, hass_client: Any
) -> None:
    registered = hass.data[frontend.DATA_PANELS][PANEL_PATH]
    assert registered.require_admin
    assert registered.sidebar_title == "Discovery Monitor"
    await async_setup_panel(hass)
    assert hass.data[frontend.DATA_PANELS][PANEL_PATH] is registered
    client = await hass_client()
    response = await client.get("/discovery_monitor_frontend/panel.js")
    assert response.status == 200
    assert "discovery-monitor-panel" in await response.text()
    assert await hass.config_entries.async_unload(panel_entry.entry_id)
    assert PANEL_PATH not in hass.data[frontend.DATA_PANELS]
    assert await hass.config_entries.async_setup(panel_entry.entry_id)
    assert hass.data[frontend.DATA_PANELS][PANEL_PATH].require_admin


@pytest.mark.parametrize(
    ("language", "expected"),
    [("en", "Open discoveries"), ("de-DE", "Offene Funde"), ("fr", "Open discoveries")],
)
async def test_panel_live_snapshot_translations_and_unsubscribe(
    hass: Any, panel_entry: Any, hass_ws_client: Any, language: str, expected: str
) -> None:
    client = await hass_ws_client()
    storage = hass.data[DOMAIN].storage
    initial_listeners = len(storage._listeners)
    snapshot = await subscribe(client, language)
    assert snapshot["texts"]["panel_open_discoveries"] == expected
    assert snapshot["findings"] == []
    assert len(storage._listeners) == initial_listeners + 1
    finding_id = await record(hass)
    snapshot = (await client.receive_json())["event"]
    assert snapshot["findings"][0]["id"] == finding_id
    assert snapshot["findings"][0]["name"] == "Living room"
    assert snapshot["findings"][0]["can_ignore_device"]
    assert snapshot["findings"][0]["can_ignore_type"]
    assert "private-id" not in json.dumps(snapshot)
    assert "private-flow" not in json.dumps(snapshot)
    await record(hass)
    snapshot = (await client.receive_json())["event"]
    assert len(snapshot["findings"]) == 1
    assert snapshot["findings"][0]["count"] == 2
    await client.send_json_auto_id({"type": "unsubscribe_events", "subscription": 1})
    assert (await client.receive_json())["success"]
    assert len(storage._listeners) == initial_listeners


@pytest.mark.parametrize("operation", ["device", "device_type"])
async def test_panel_ignore_and_unignore_reuse_monitor(
    hass: Any, panel_entry: Any, hass_ws_client: Any, operation: str
) -> None:
    finding_id = await record(hass)
    client = await hass_ws_client()
    monitor = hass.data[DOMAIN]
    with patch.object(hass.config_entries.flow, "async_abort") as abort:
        assert (await action(client, operation, finding_id))["success"]
    abort.assert_called_once_with("private-flow")
    assert not monitor.storage.visible_findings()
    rule_id = monitor.storage.rule_id(monitor.storage.rules[0])
    assert (await action(client, "unignore", rule_id))["success"]
    assert len(monitor.storage.visible_findings()) == 1
    assert not monitor.storage.rules


async def test_panel_dismiss_preserves_history_without_ignoring(
    hass: Any, panel_entry: Any, hass_ws_client: Any
) -> None:
    finding_id = await record(hass)
    client = await hass_ws_client()
    assert (await action(client, "dismiss", finding_id))["success"]
    storage = hass.data[DOMAIN].storage
    assert not storage.visible_findings()
    assert finding_id in storage.findings
    assert not storage.rules
    response = await action(client, "device", finding_id)
    assert response["error"]["code"] == "stale_target"


async def test_panel_rejects_unsafe_missing_and_invalid_actions(
    hass: Any, panel_entry: Any, hass_ws_client: Any
) -> None:
    finding_id = await record(hass, safe=False)
    client = await hass_ws_client()
    snapshot = await subscribe(client)
    assert not snapshot["findings"][0]["can_ignore_device"]
    assert not snapshot["findings"][0]["can_ignore_type"]
    for operation, target in [
        ("device", finding_id),
        ("device_type", finding_id),
        ("dismiss", "missing"),
        ("unignore", "missing"),
    ]:
        assert (await action(client, operation, target))["error"]["code"] == (
            "stale_target"
        )
    assert (await action(client, "delete", finding_id))["error"]["code"] == (
        "invalid_format"
    )
    assert not hass.data[DOMAIN].storage.rules


async def test_panel_unload_detaches_clients_and_disables_commands(
    hass: Any, panel_entry: Any, hass_ws_client: Any
) -> None:
    client = await hass_ws_client()
    await subscribe(client)
    monitor = hass.data[DOMAIN]
    assert await hass.config_entries.async_unload(panel_entry.entry_id)
    assert not (await client.receive_json())["event"]["available"]
    assert not monitor.storage._listeners
    assert (await action(client, "dismiss", "missing"))["error"]["code"] == (
        "not_loaded"
    )
    await client.send_json_auto_id({"type": f"{DOMAIN}/subscribe"})
    assert (await client.receive_json())["error"]["code"] == "not_loaded"


async def test_panel_denies_non_admin_read_and_write(
    hass: Any, panel_entry: Any, hass_ws_client: Any, hass_read_only_access_token: str
) -> None:
    client = await hass_ws_client(access_token=hass_read_only_access_token)
    await client.send_json_auto_id({"type": f"{DOMAIN}/subscribe"})
    assert (await client.receive_json())["error"]["code"] == "unauthorized"
    assert (await action(client, "dismiss", "missing"))["error"]["code"] == (
        "unauthorized"
    )


async def test_panel_reports_persistence_failure_without_success(
    hass: Any, panel_entry: Any, hass_ws_client: Any
) -> None:
    finding_id = await record(hass)
    client = await hass_ws_client()
    with patch.object(
        hass.data[DOMAIN].storage,
        "async_dismiss_finding",
        AsyncMock(side_effect=OSError("Disk unavailable")),
    ):
        result = await action(client, "dismiss", finding_id)
    assert not result["success"]
