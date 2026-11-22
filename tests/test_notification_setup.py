from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
import voluptuous as vol
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.discovery_monitor import CONFIG_SCHEMA, async_setup
from custom_components.discovery_monitor.const import DOMAIN


@pytest.mark.parametrize("hours", [-1, 721, "invalid"])
def test_yaml_rejects_invalid_pause(hours: Any) -> None:
    with pytest.raises(vol.Invalid):
        CONFIG_SCHEMA({DOMAIN: {"notification_pause_hours": hours}})


async def test_options_save_applies_live_and_survives_reload(
    hass: Any, enable_custom_integrations: Any
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    monitor = hass.data[DOMAIN]
    key = "a" * 64
    monitor.notifications.async_event_emitted(key)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "notifications"}
    )
    with patch.object(hass.config_entries.flow, "async_abort") as abort:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "notification_pause_hours": 24,
                "debug_logging": True,
                "reset_pauses": True,
            },
        )
    await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert hass.data[DOMAIN] is monitor
    assert monitor.options["notification_pause_hours"] == 24
    assert monitor.options["debug_logging"] is True
    assert not monitor.notifications.last_emitted
    assert "reset_pauses" not in entry.options
    abort.assert_not_called()
    monitor.notifications.async_event_emitted(key)
    await monitor.notifications._store.async_save(
        dict(monitor.notifications.last_emitted)
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    assert hass.data[DOMAIN] is not monitor
    assert hass.data[DOMAIN].options["notification_pause_hours"] == 24
    assert hass.data[DOMAIN].notifications.is_paused(key, 24)


async def test_ui_options_preserve_yaml_filters(
    hass: Any, enable_custom_integrations: Any
) -> None:
    assert await async_setup(
        hass, CONFIG_SCHEMA({DOMAIN: {"include_sources": "bluetooth"}})
    )
    monitor = hass.data[DOMAIN]
    entry = MockConfigEntry(
        domain=DOMAIN, data={}, options={"notification_pause_hours": 12}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert hass.data[DOMAIN] is monitor
    assert monitor.options["include_sources"] == "bluetooth"
    assert monitor.options["notification_pause_hours"] == 12
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert hass.data[DOMAIN] is monitor
