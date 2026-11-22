from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.discovery_monitor.const import (
    CONF_DEBUG_LOGGING,
    CONF_NOTIFICATION_PAUSE_HOURS,
    DOMAIN,
)
from custom_components.discovery_monitor.notifications import (
    MAX_NOTIFICATION_IDENTITIES,
    NotificationHistory,
    notification_key,
)

from .test_bluetooth_models import THERMOPRO
from .test_monitor import add_flow, make_monitor
from .test_storage import make_store


def make_history() -> NotificationHistory:
    history = object.__new__(NotificationHistory)
    history._store = MagicMock(
        async_load=AsyncMock(return_value=None), async_save=AsyncMock()
    )
    history.last_emitted = {}
    return history


def bluetooth_flow(
    manager: Any, flow: str, address: str = "C0:11:22:33:44:55", **extra: Any
) -> None:
    add_flow(
        manager,
        flow_id=flow,
        domain="thermopro",
        source="bluetooth",
        unique_id=address,
        init_data=THERMOPRO | {"address": address} | extra,
    )
    manager.listener("added", flow)


@pytest.mark.parametrize("address_type", [None, "public", "random_static", "static"])
def test_notification_identity_normalizes_same_address_only(
    address_type: str | None,
) -> None:
    device = {"domain": "thermopro", "unique_id": "C0:11:22:33:44:55"}
    key = notification_key(
        "thermopro", "bluetooth", device, {"address_type": address_type}
    )
    assert key
    assert key == notification_key(
        "thermopro", "bluetooth", device | {"unique_id": "c01122334455"}, {}
    )
    assert key != notification_key("govee_ble", "bluetooth", device, {})
    assert key != notification_key(
        "thermopro", "bluetooth", device | {"unique_id": "C0:11:22:33:44:56"}, {}
    )


@pytest.mark.parametrize(
    "data",
    [
        {"address_type": "random"},
        {"address_type": "resolvable_private"},
        {"address_type": "non_resolvable_private"},
        {"address_type": []},
        {"address": "C0:11:22:33:44:56"},
    ],
)
def test_uncertain_identity_does_not_pause(data: dict[str, Any]) -> None:
    assert (
        notification_key(
            "thermopro", "bluetooth", {"unique_id": "C0:11:22:33:44:55"}, data
        )
        is None
    )
    assert notification_key("thermopro", "bluetooth", None, data) is None
    assert (
        notification_key(
            "thermopro", "bluetooth", {"hardware_id": "C0:11:22:33:44:55"}, data
        )
        is None
    )


async def test_default_is_off_and_repeats_keep_event_payload() -> None:
    monitor, manager, bus = make_monitor()
    monitor.notifications = make_history()
    monitor.storage = make_store()
    bluetooth_flow(manager, "one")
    bluetooth_flow(manager, "two")
    await asyncio.sleep(0)
    assert bus.async_fire.call_count == 2
    assert not manager.aborted
    first, second = [call.args[1] for call in bus.async_fire.call_args_list]
    assert first | {"flow_id": "two"} == second
    assert set(first) == {
        "domain",
        "source",
        "title",
        "unique_id",
        "flow_id",
        "device_name",
        "address",
        "discovery_data",
    }
    assert "manufacturer_data" not in first["discovery_data"]
    assert len(monitor.notifications.last_emitted) == 1


async def test_pause_uses_last_emitted_event_and_preserves_counters_and_flows() -> None:
    monitor, manager, bus = make_monitor({CONF_NOTIFICATION_PAUSE_HOURS: 2})
    history = monitor.notifications = make_history()
    storage = monitor.storage = make_store()
    with patch(
        "custom_components.discovery_monitor.notifications.time.time",
        return_value=100000,
    ) as clock:
        bluetooth_flow(manager, "first")
        original = dict(history.last_emitted)
        clock.return_value += 3600
        bluetooth_flow(manager, "repeat")
        assert history.last_emitted == original
        assert bus.async_fire.call_count == 1
        clock.return_value += 3600
        bluetooth_flow(manager, "expired")
        assert bus.async_fire.call_count == 2
        assert next(iter(history.last_emitted.values())) == clock.return_value
        bluetooth_flow(manager, "different", "C0:11:22:33:44:56")
        assert bus.async_fire.call_count == 3
    await asyncio.sleep(0)
    assert not manager.aborted
    assert sorted(item["count"] for item in storage.findings.values()) == [1, 3]
    assert storage._store.save_calls == 0
    assert history._store.async_save.await_count == 0
    assert not storage.rules


async def test_pause_survives_real_store_reload_and_can_be_reset(hass: Any) -> None:
    history = NotificationHistory(hass)
    await history.async_load()
    key = "a" * 64
    history.async_event_emitted(key)
    # HA flushes pending delayed saves on a normal shutdown. Force that same
    # Store boundary here, then construct a fresh instance to simulate startup.
    await history._store.async_save(dict(history.last_emitted))
    reloaded = NotificationHistory(hass)
    await reloaded.async_load()
    assert reloaded.is_paused(key, 24)
    assert not reloaded.is_paused("b" * 64, 24)
    assert not reloaded.is_paused(key, 0)
    await reloaded.async_reset()
    third = NotificationHistory(hass)
    await third.async_load()
    assert not third.is_paused(key, 24)


async def test_reenabling_pause_uses_last_actual_event_even_while_disabled() -> None:
    monitor, manager, bus = make_monitor({CONF_NOTIFICATION_PAUSE_HOURS: 2})
    history = monitor.notifications = make_history()
    with patch(
        "custom_components.discovery_monitor.notifications.time.time",
        return_value=100000,
    ) as clock:
        bluetooth_flow(manager, "one")
        clock.return_value += 3600
        monitor.async_update_options({CONF_NOTIFICATION_PAUSE_HOURS: 0})
        bluetooth_flow(manager, "disabled")
        assert next(iter(history.last_emitted.values())) == clock.return_value
        clock.return_value += 3600
        monitor.async_update_options({CONF_NOTIFICATION_PAUSE_HOURS: 2})
        bluetooth_flow(manager, "enabled")
        assert bus.async_fire.call_count == 2
    assert not manager.aborted


async def test_reset_pending_save_does_not_restore_old_pauses(hass: Any) -> None:
    history = NotificationHistory(hass)
    history.async_event_emitted("a" * 64)
    await history.async_reset()
    reloaded = NotificationHistory(hass)
    await reloaded.async_load()
    assert not reloaded.last_emitted


async def test_pause_bounds_and_invalid_persisted_data() -> None:
    history = make_history()
    with patch(
        "custom_components.discovery_monitor.notifications.time.time",
        return_value=10000000,
    ):
        history._store.async_load.return_value = {
            "a" * 64: 10000000,
            "b" * 64: float("nan"),
            "c" * 64: True,
            "d" * 64: 10000001,
            "e" * 64: 1,
            "bad-key": 10000000,
        }
        await history.async_load()
        assert history.last_emitted == {"a" * 64: 10000000}
        for value in range(MAX_NOTIFICATION_IDENTITIES + 1):
            history.async_event_emitted(f"{value:064x}")
        assert len(history.last_emitted) == MAX_NOTIFICATION_IDENTITIES


async def test_model_rule_stops_only_explicitly_selected_model_after_restart() -> None:
    monitor, manager, bus = make_monitor({CONF_NOTIFICATION_PAUSE_HOURS: 24})
    storage = monitor.storage = make_store()
    monitor.notifications = make_history()
    bluetooth_flow(manager, "first")
    await asyncio.sleep(0)
    assert not storage.rules
    finding_id = next(iter(storage.findings))
    assert await monitor.async_ignore_finding(finding_id, "model")
    assert manager.aborted == ["first"]
    reloaded = make_store()
    reloaded._store.async_load = AsyncMock(return_value=deepcopy(storage._store.saved))
    await reloaded.async_load()
    monitor.storage = reloaded
    bluetooth_flow(manager, "matching-new-address", "C0:11:22:33:44:56")
    assert manager.aborted == ["first", "matching-new-address"]
    assert bus.async_fire.call_count == 1
    bluetooth_flow(manager, "different-model", "C0:11:22:33:44:57", name="TP358")
    bluetooth_flow(manager, "incomplete", "C0:11:22:33:44:58", manufacturer_data={})
    assert bus.async_fire.call_count == 3
    assert len(manager.aborted) == 2
    await asyncio.sleep(0)


async def test_debug_decision_explains_pause_without_sensitive_data(
    caplog: Any,
) -> None:
    monitor, manager, _ = make_monitor(
        {CONF_NOTIFICATION_PAUSE_HOURS: 24, CONF_DEBUG_LOGGING: True}
    )
    monitor.notifications = make_history()
    with caplog.at_level(logging.DEBUG, logger=f"custom_components.{DOMAIN}.monitor"):
        bluetooth_flow(manager, "private-flow-1", token="secret-token")
        bluetooth_flow(manager, "private-flow-2", token="secret-token")
    assert "reason=event_emitted" in caplog.text
    assert "reason=notification_pause" in caplog.text
    assert "identity_source=unique_id" in caplog.text
    assert "model_result=model_recognized" in caplog.text
    assert "checked_rules=" in caplog.text
    for sensitive in (
        "C0:11:22:33:44:55",
        "ABCD",
        "private-flow",
        "secret-token",
        "manufacturer_data",
    ):
        assert sensitive not in caplog.text
    caplog.clear()
    monitor.async_update_options({CONF_DEBUG_LOGGING: False})
    with caplog.at_level(logging.DEBUG, logger=f"custom_components.{DOMAIN}.monitor"):
        bluetooth_flow(manager, "third")
    assert "Discovery decision" not in caplog.text
