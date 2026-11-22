"""Run with ``python -m tests.ha_compatibility`` without version-bound pytest fixtures.

Only discovery-handler construction is stubbed. The Home Assistant runtime,
flow manager, callbacks, event bus and Store implementation are real.
"""

from __future__ import annotations

import unittest
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import AsyncMock, patch

# HA 2026.11 installs its supported voluptuous/httpx aliases during startup.
import homeassistant  # noqa: F401  # isort: skip

import voluptuous as vol
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigEntries, ConfigFlow, ConfigFlowResult
from homeassistant.const import __version__ as ha_version
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import UnknownFlow

from custom_components.discovery_monitor import CONFIG_SCHEMA, async_setup
from custom_components.discovery_monitor.bluetooth_models import detect_model
from custom_components.discovery_monitor.const import DOMAIN, EVENT_DISCOVERED
from custom_components.discovery_monitor.notifications import NotificationHistory
from custom_components.discovery_monitor.storage import DiscoveryStore

from .notification_races import check_reset_and_restart


def bluetooth_info(
    address: str = "C0:11:22:33:44:55",
    *,
    name: str | None = "TP357S (ABCD)",
    manufacturer_data: dict[int, bytes] | None = None,
    device_name: str | None = None,
    connectable: bool = False,
) -> BluetoothServiceInfoBleak:
    """Construct the real HA object through its scanner conversion API."""
    return BluetoothServiceInfoBleak.from_scan(
        "test-scanner",
        BLEDevice(address, device_name, {}),
        AdvertisementData(
            name,
            {194: bytes(5)} if manufacturer_data is None else manufacturer_data,
            {},
            [],
            None,
            -60,
            (),
        ),
        1.0,
        connectable,
    )


class BluetoothProbeFlow(ConfigFlow):
    """Supply a confirmation step using the same address identity as ThermoPro."""

    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self.init_step = "bluetooth"

    async def async_step_bluetooth(self, data: Any) -> ConfigFlowResult:
        await self.async_set_unique_id(data.address)
        return await self.async_step_confirm()

    async def async_step_confirm(self, _data: Any = None) -> ConfigFlowResult:
        return self.async_show_form(step_id="confirm", data_schema=vol.Schema({}))


class HomeAssistantCompatibility(unittest.IsolatedAsyncioTestCase):
    async def test_notification_reset_races_and_real_restart(self) -> None:
        for in_flight in (False, True):
            for emit_during_reset in (False, True):
                for resets in (1, 2):
                    with self.subTest(
                        in_flight=in_flight, new_event=emit_during_reset, resets=resets
                    ):
                        await check_reset_and_restart(
                            in_flight=in_flight,
                            emit_during_reset=emit_during_reset,
                            resets=resets,
                        )

    def test_real_bluetooth_objects_model_evidence(self) -> None:
        samples = [
            (
                "victron_ble",
                None,
                {0x02E1: bytes.fromhex("100053a001000000") + bytes(16)},
                "SmartSolar MPPT 75/15",
            ),
            (
                "govee_ble",
                "GVH5105_ABCD",
                {1: bytes.fromhex("010103325e64")},
                "Govee H5105",
            ),
            ("thermopro", "TP357S (ABCD)", {194: bytes(5)}, "ThermoPro TP357S"),
        ]
        for domain, name, data, label in samples:
            for connectable in (False, True):
                with self.subTest(domain=domain, connectable=connectable):
                    info = bluetooth_info(
                        name=name, manufacturer_data=data, connectable=connectable
                    )
                    assert isinstance(info, BluetoothServiceInfoBleak)
                    result = detect_model(domain, "bluetooth", info)
                    assert result.label == label
                    assert result.reason == "model_recognized"
            missing = detect_model(
                domain, "bluetooth", bluetooth_info(name=name, manufacturer_data={})
            )
            assert missing.criteria is None
            assert missing.reason == "missing_manufacturer_data"

    def test_missing_names_and_unknown_models_are_distinguished(self) -> None:
        cases = [
            (
                "govee_ble",
                None,
                {1: bytes.fromhex("010103325e64")},
                "missing_model_name",
            ),
            (
                "govee_ble",
                "GVH9999_ABCD",
                {1: bytes.fromhex("010103325e64")},
                "unsupported_model",
            ),
            ("govee_ble", "GVH5105_ABCD", {1: b"\x01\x01"}, "incomplete_advertisement"),
            ("thermopro", None, {194: bytes(5)}, "missing_model_name"),
            ("thermopro", "TP999 (ABCD)", {194: bytes(5)}, "unsupported_model"),
            ("thermopro", "TP357S (ABCD)", {194: bytes(4)}, "incomplete_advertisement"),
            (
                "victron_ble",
                None,
                {0x02E1: bytes.fromhex("1000ffff01000000") + bytes(16)},
                "unsupported_model",
            ),
            (
                "victron_ble",
                None,
                {0x02E1: bytes.fromhex("100053a001")},
                "incomplete_advertisement",
            ),
        ]
        for domain, name, data, reason in cases:
            with self.subTest(domain=domain, reason=reason):
                result = detect_model(
                    domain,
                    "bluetooth",
                    bluetooth_info(name=name, manufacturer_data=data),
                )
                assert result.criteria is None
                assert result.reason == reason

    def test_cached_device_name_is_not_advertised_model_evidence(self) -> None:
        for domain, name, data in [
            ("govee_ble", "GVH5105_ABCD", {1: bytes.fromhex("010103325e64")}),
            ("thermopro", "TP357S (ABCD)", {194: bytes(5)}),
        ]:
            with self.subTest(domain=domain):
                info = bluetooth_info(
                    name=None, device_name=name, manufacturer_data=data
                )
                assert info.name == name  # HA falls back to the cached BLEDevice name.
                assert info.advertisement.local_name is None
                result = detect_model(domain, "bluetooth", info)
                assert result.criteria is None
                assert result.reason == "missing_model_name"

    async def test_discovery_callbacks_storage_and_notifications(self) -> None:  # noqa: PLR0915
        with TemporaryDirectory(prefix="discovery-compat-") as directory:
            hass = HomeAssistant(directory)
            hass.config_entries = ConfigEntries(hass, {})
            assert await async_setup(
                hass, CONFIG_SCHEMA({DOMAIN: {"notification_pause_hours": 24}})
            )
            monitor = hass.data[DOMAIN]
            events = []
            unsubscribe = hass.bus.async_listen(EVENT_DISCOVERED, events.append)
            manager = hass.config_entries.flow

            async def discover(address: str, **extra: Any) -> str:
                data = bluetooth_info(address, **extra)
                with patch.object(
                    manager,
                    "async_create_flow",
                    AsyncMock(return_value=BluetoothProbeFlow()),
                ):
                    result = await manager.async_init(
                        "thermopro", context={"source": "bluetooth"}, data=data
                    )
                await hass.async_block_till_done()
                return result["flow_id"]

            try:
                first = await discover("C0:11:22:33:44:55")
                assert len(events) == 1
                assert len(monitor.storage.visible_findings()) == 1
                manager.async_abort(first)
                await hass.async_block_till_done()
                second = await discover("C0:11:22:33:44:55")
                assert len(events) == 1
                assert manager.async_get(second)
                assert next(iter(monitor.storage.findings.values()))["count"] == 2
                await monitor.notifications._store.async_save(
                    dict(monitor.notifications.last_emitted)
                )
                reloaded = NotificationHistory(hass)
                await reloaded.async_load()
                monitor.notifications = reloaded
                manager.async_abort(second)
                await hass.async_block_till_done()
                third = await discover("C0:11:22:33:44:55")
                assert len(events) == 1
                assert manager.async_get(third)
                await reloaded.async_reset()
                manager.async_abort(third)
                await hass.async_block_till_done()
                current = await discover("C0:11:22:33:44:55")
                assert len(events) == 2
                await discover("C0:11:22:33:44:56")
                assert len(events) == 3
                fid = next(iter(monitor.storage.findings))
                assert await monitor.async_ignore_finding(fid, "model")
                with self.assertRaises(UnknownFlow):  # noqa: PT027 - no pytest dependency
                    manager.async_get(current)
                restored = DiscoveryStore(hass)
                await restored.async_load()
                monitor.storage = restored
                ignored = await discover("C0:11:22:33:44:57")
                assert len(events) == 3
                with self.assertRaises(UnknownFlow):  # noqa: PT027 - no pytest dependency
                    manager.async_get(ignored)
                incomplete = await discover("C0:11:22:33:44:58", manufacturer_data={})
                assert len(events) == 4
                assert manager.async_get(incomplete)
                assert await restored.async_remove_rule(
                    restored.rule_id(restored.rules[0])
                )
                # This address was already logged while the model rule applied,
                # but never emitted an event. Its stored finding must not act as
                # an implicit ignore rule after explicit rule removal.
                await discover("C0:11:22:33:44:57")
                assert len(events) == 5
                repeated = next(
                    finding
                    for finding in restored.findings.values()
                    if finding["device_criteria"]["unique_id"] == "c0:11:22:33:44:57"
                )
                assert repeated["count"] == 2
                monitor.async_stop()
                await discover("C0:11:22:33:44:60")
                assert len(events) == 5
            finally:
                monitor.async_stop()
                unsubscribe()
                await hass.async_stop(force=True)


if __name__ == "__main__":
    print(f"Home Assistant {ha_version}: native discovery compatibility")  # noqa: T201
    unittest.main()
