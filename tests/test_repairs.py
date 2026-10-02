from __future__ import annotations

from typing import Any

import pytest
from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component

from custom_components.discovery_monitor.const import DOMAIN
from custom_components.discovery_monitor.identity import DEVICE
from custom_components.discovery_monitor.repairs import async_create_discovery_issue


@pytest.mark.asyncio
async def test_discovery_repair_flow_opens(
    hass: Any, enable_custom_integrations: Any
) -> None:
    assert await async_setup_component(hass, "repairs", {})
    assert await async_setup_component(hass, DOMAIN, {DOMAIN: {}})
    async_create_discovery_issue(
        hass,
        "finding-1",
        "Example device",
        can_ignore_device=True,
        can_ignore_type=True,
    )

    manager = repairs_flow_manager(hass)
    assert manager is not None
    result = await manager.async_init(DOMAIN, data={"issue_id": "finding-1"})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    monitor = hass.data[DOMAIN]
    monitor.storage.findings["finding-1"] = {
        "label": "Example device",
        "device_criteria": {"domain": "example", "unique_id": "device-1"},
        "type_criteria": None,
    }
    completed = await manager.async_configure(
        result["flow_id"], {"action": DEVICE}
    )

    assert completed["type"] is FlowResultType.CREATE_ENTRY
    assert monitor.storage.rules[0]["kind"] == DEVICE
