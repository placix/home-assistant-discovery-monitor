from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.helpers import entity_registry as er

from custom_components.discovery_monitor import CONFIG_SCHEMA, async_setup
from custom_components.discovery_monitor.const import DOMAIN, EVENT_DISCOVERED
from custom_components.discovery_monitor.identity import device_criteria, type_criteria
from custom_components.discovery_monitor.monitor import (
    DiscoveryMonitor,
    extract_safe_discovery_data,
)


@dataclass
class BluetoothInfo:
    name: str
    address: str
    manufacturer: str
    service_uuids: list[str]
    token: str


@dataclass
class DhcpInfo:
    hostname: str
    ip_address: str
    mac_address: str


class FakeFlowManager:
    def __init__(self) -> None:
        self.listener = None
        self.flows: dict[str, dict[str, Any]] = {}
        self._progress: dict[str, Any] = {}
        self.unsubscribed = False
        self.aborted: list[str] = []

    def async_subscribe_flow(self, listener: Any) -> Any:
        self.listener = listener

        def unsubscribe() -> None:
            self.unsubscribed = True
            self.listener = None

        return unsubscribe

    def async_get(self, flow_id: str) -> dict[str, Any]:
        return self.flows[flow_id]

    def async_abort(self, flow_id: str) -> None:
        self.aborted.append(flow_id)


def make_monitor(options: dict[str, Any] | None = None) -> tuple[Any, Any, Any]:
    manager = FakeFlowManager()
    bus = SimpleNamespace(async_fire=MagicMock())
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(flow=manager),
        bus=bus,
    )
    monitor = DiscoveryMonitor(hass, options or {})
    monitor.async_start()
    return monitor, manager, bus


def add_flow(
    manager: FakeFlowManager,
    *,
    flow_id: str,
    domain: str,
    source: str,
    init_data: Any,
    unique_id: str | None = None,
    placeholders: dict[str, str] | None = None,
) -> None:
    context: dict[str, Any] = {"source": source}
    if unique_id is not None:
        context["unique_id"] = unique_id
    manager.flows[flow_id] = {
        "flow_id": flow_id,
        "handler": domain,
        "context": context,
        "step_id": "confirm",
    }
    manager._progress[flow_id] = SimpleNamespace(
        init_data=init_data,
        cur_step={"description_placeholders": placeholders or {}},
    )


@pytest.mark.asyncio
async def test_setup_starts_monitor() -> None:
    manager = FakeFlowManager()
    bus = SimpleNamespace(
        async_fire=MagicMock(),
        async_listen_once=MagicMock(),
    )
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(flow=manager),
        bus=bus,
        data={},
    )

    assert await async_setup(hass, CONFIG_SCHEMA({DOMAIN: {}}))
    assert isinstance(hass.data[DOMAIN], DiscoveryMonitor)
    assert manager.listener is not None
    bus.async_listen_once.assert_called_once()


def test_stop_removes_listener() -> None:
    monitor, manager, _ = make_monitor()

    monitor.async_stop()

    assert manager.unsubscribed


def test_yaml_schema_preserves_monitor_options() -> None:
    config = CONFIG_SCHEMA(
        {
            DOMAIN: {
                "enabled": True,
                "include_sources": "bluetooth, dhcp",
                "exclude_domains": "test",
            }
        }
    )

    assert config[DOMAIN] == {
        "enabled": True,
        "include_discovery_data": True,
        "debug_logging": False,
        "include_sources": "bluetooth, dhcp",
        "exclude_sources": "",
        "include_domains": "",
        "exclude_domains": "test",
    }


@pytest.mark.asyncio
async def test_setup_registers_no_entities(hass: Any) -> None:
    assert await async_setup(hass, CONFIG_SCHEMA({DOMAIN: {}}))

    registry = er.async_get(hass)
    assert not [
        entry for entry in registry.entities.values() if entry.platform == DOMAIN
    ]
    assert not [
        state
        for state in hass.states.async_all()
        if state.entity_id.startswith(f"{DOMAIN}.")
    ]

    hass.data[DOMAIN].async_stop()


def test_bluetooth_discovery_flow() -> None:
    _, manager, bus = make_monitor()
    add_flow(
        manager,
        flow_id="ble-flow",
        domain="led_ble",
        source="bluetooth",
        unique_id="AA:BB:CC:DD:EE:FF",
        init_data=BluetoothInfo(
            name="QHM-B051",
            address="AA:BB:CC:DD:EE:FF",
            manufacturer="QHM",
            service_uuids=["0000ffe0-0000-1000-8000-00805f9b34fb"],
            token="must-not-leak",
        ),
        placeholders={"name": "QHM-B051"},
    )

    manager.listener("added", "ble-flow")

    bus.async_fire.assert_called_once()
    event_type, payload = bus.async_fire.call_args.args
    assert event_type == EVENT_DISCOVERED
    assert payload["domain"] == "led_ble"
    assert payload["source"] == "bluetooth"
    assert payload["title"] == "QHM-B051"
    assert payload["address"] == "AA:BB:CC:DD:EE:FF"
    assert payload["unique_id"] == "AA:BB:CC:DD:EE:FF"


def test_non_bluetooth_discovery_flow() -> None:
    _, manager, bus = make_monitor()
    add_flow(
        manager,
        flow_id="dhcp-flow",
        domain="example_router",
        source="dhcp",
        init_data=DhcpInfo(
            hostname="router.local",
            ip_address="192.0.2.10",
            mac_address="00:11:22:33:44:55",
        ),
    )

    manager.listener("added", "dhcp-flow")

    payload = bus.async_fire.call_args.args[1]
    assert payload["source"] == "dhcp"
    assert payload["device_name"] == "router.local"
    assert payload["address"] == "00:11:22:33:44:55"


def test_manual_flow_does_not_fire_event() -> None:
    _, manager, bus = make_monitor()
    add_flow(
        manager,
        flow_id="manual-flow",
        domain="example",
        source="user",
        init_data={},
    )

    manager.listener("added", "manual-flow")

    bus.async_fire.assert_not_called()


def test_missing_optional_data_is_not_invented() -> None:
    _, manager, bus = make_monitor()
    add_flow(
        manager,
        flow_id="usb-flow",
        domain="example_usb",
        source="usb",
        init_data=None,
    )

    manager.listener("added", "usb-flow")

    payload = bus.async_fire.call_args.args[1]
    assert payload["title"] is None
    assert payload["unique_id"] is None
    assert payload["device_name"] is None
    assert payload["address"] is None
    assert payload["discovery_data"] == {}


def test_secrets_are_never_emitted() -> None:
    _, manager, bus = make_monitor()
    add_flow(
        manager,
        flow_id="mqtt-flow",
        domain="example_mqtt",
        source="mqtt",
        init_data={
            "name": "Safe device",
            "topic": "home/safe/device",
            "password": "bad",
            "access_token": "bad",
            "api_key": "bad",
            "nested": {"secret": "bad"},
        },
        placeholders={"name": "Safe device", "token": "bad"},
    )

    manager.listener("added", "mqtt-flow")

    payload = bus.async_fire.call_args.args[1]
    serialized = repr(payload).lower()
    assert "bad" not in serialized
    assert payload["discovery_data"] == {
        "name": "Safe device",
        "topic": "home/safe/device",
    }


def test_duplicate_callback_fires_once() -> None:
    _, manager, bus = make_monitor()
    add_flow(
        manager,
        flow_id="same-flow",
        domain="example",
        source="zeroconf",
        init_data={"name": "Example"},
    )

    manager.listener("added", "same-flow")
    manager.listener("added", "same-flow")

    bus.async_fire.assert_called_once()


def test_controlled_extraction_rejects_objects_and_unknown_fields() -> None:
    unsafe = object()
    result = extract_safe_discovery_data(
        {
            "name": "Device",
            "address": unsafe,
            "arbitrary": "not allowlisted",
            "service_uuids": ["one", unsafe, "two"],
        }
    )

    assert result == {"name": "Device", "service_uuids": ["one", "two"]}


def test_slot_based_service_info_is_safely_extracted() -> None:
    class SlotInfo:
        __slots__ = ("address", "name", "password")

        def __init__(self) -> None:
            self.address = "AA:BB:CC:DD:EE:FF"
            self.name = "Slot device"
            self.password = "must-not-leak"

    assert extract_safe_discovery_data(SlotInfo()) == {
        "address": "AA:BB:CC:DD:EE:FF",
        "name": "Slot device",
    }


def test_unique_id_has_priority_for_device_identity() -> None:
    assert device_criteria(
        "example", "dhcp", " Stable-ID ", {"mac_address": "00:11:22:33:44:55"}
    ) == {"domain": "example", "unique_id": "stable-id"}


def test_device_identity_never_falls_back_to_ip_or_random_bluetooth() -> None:
    assert device_criteria(
        "example", "dhcp", None, {"ip_address": "192.0.2.1"}
    ) is None
    assert device_criteria(
        "example", "bluetooth", None, {"address": "C2:11:22:33:44:55"}
    ) is None
    assert device_criteria(
        "example",
        "bluetooth",
        None,
        {"address": "00:11:22:33:44:55", "address_type": "random"},
    ) is None


def test_source_specific_stable_device_identities() -> None:
    assert device_criteria(
        "router", "dhcp", None, {"mac_address": "02:11:22:33:44:55"}
    ) == {"domain": "router", "mac_address": "02:11:22:33:44:55"}
    assert device_criteria(
        "media", "ssdp", None, {"ssdp_usn": "uuid:device-1::urn:example"}
    ) == {"domain": "media", "ssdp_usn": "uuid:device-1"}
    assert device_criteria(
        "homekit_controller", "homekit", None, {"id": "11:22:33:44:55:66"}
    ) == {"domain": "homekit_controller", "id": "11:22:33:44:55:66"}


def test_type_identity_excludes_device_specific_and_volatile_values() -> None:
    criteria = type_criteria(
        "light",
        "zeroconf",
        {
            "model": "Bulb 2",
            "manufacturer": "Example",
            "service_type": "_example._tcp.local.",
            "serial_number": "device-1",
            "mac_address": "00:11:22:33:44:55",
            "ip_address": "192.0.2.2",
            "rssi": -40,
        },
    )

    assert criteria == {
        "domain": "light",
        "model": "bulb 2",
        "manufacturer": "example",
        "service_type": "_example._tcp.local.",
    }


def test_type_identity_is_not_created_from_manufacturer_alone() -> None:
    assert type_criteria(
        "example", "dhcp", {"manufacturer": "Example", "hostname": "device"}
    ) is None


@pytest.mark.asyncio
async def test_matching_ignore_rule_aborts_flow_but_still_logs() -> None:
    monitor, manager, bus = make_monitor()

    class FakeStorage:
        def __init__(self) -> None:
            self.recorded = False

        def matching_rule(
            self, _device: Any, _device_type: Any
        ) -> dict[str, Any]:
            return {"kind": "device"}

        async def async_record(self, **_kwargs: Any) -> str:
            self.recorded = True
            return "finding"

    storage = FakeStorage()
    monitor.storage = storage
    add_flow(
        manager,
        flow_id="ignored-flow",
        domain="example",
        source="dhcp",
        unique_id="device-1",
        init_data={"hostname": "Ignored device", "ip_address": "192.0.2.2"},
    )

    manager.listener("added", "ignored-flow")
    await asyncio.sleep(0)

    assert manager.aborted == ["ignored-flow"]
    assert storage.recorded
    bus.async_fire.assert_not_called()
