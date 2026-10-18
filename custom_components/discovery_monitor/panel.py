"""Admin-only discovery management panel and WebSocket API."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.translation import async_get_translations

from .const import DOMAIN
from .identity import DEVICE, DEVICE_TYPE

if TYPE_CHECKING:
    from .storage import DiscoveryStore

PANEL_PATH = "discovery-monitor"
_DATA_PANEL = f"{DOMAIN}_panel"
_STATIC_URL = "/discovery_monitor_frontend/panel.js"


@dataclass
class PanelRuntime:
    """Track one-time registration and active panel subscriptions."""

    active: bool = False
    close_subscriptions: set[CALLBACK_TYPE] = field(default_factory=set)


async def async_setup_panel(hass: HomeAssistant) -> None:
    """Register the module once, and show the panel while a UI entry is loaded."""
    if _DATA_PANEL not in hass.data:
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    _STATIC_URL,
                    str(Path(__file__).parent / "frontend" / "panel.js"),
                    cache_headers=False,
                )
            ]
        )
        websocket_api.async_register_command(hass, websocket_subscribe)
        websocket_api.async_register_command(hass, websocket_action)
        hass.data[_DATA_PANEL] = PanelRuntime()
    runtime: PanelRuntime = hass.data[_DATA_PANEL]
    if runtime.active:
        return
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_PATH,
        webcomponent_name="discovery-monitor-panel",
        sidebar_title="Discovery Monitor",
        sidebar_icon="mdi:radar",
        module_url=_STATIC_URL,
        require_admin=True,
    )
    runtime.active = True


@callback
def async_unload_panel(hass: HomeAssistant) -> None:
    """Remove the panel and detach its storage listeners on entry unload."""
    if (runtime := hass.data.get(_DATA_PANEL)) is None or not runtime.active:
        return
    runtime.active = False
    for close in tuple(runtime.close_subscriptions):
        close()
    frontend.async_remove_panel(hass, PANEL_PATH)


def _storage(hass: HomeAssistant) -> DiscoveryStore | None:
    runtime = hass.data.get(_DATA_PANEL)
    if runtime is None or not runtime.active:
        return None
    return getattr(hass.data.get(DOMAIN), "storage", None)


def _snapshot(storage: DiscoveryStore) -> dict[str, Any]:
    """Expose display data and opaque action references, never match criteria."""
    findings = [
        {
            "id": finding_id,
            "name": finding.get("label") or finding.get("domain") or "",
            "integration": finding.get("domain", ""),
            "source": finding.get("source", ""),
            "count": max(int(finding.get("count", 1)), 1),
            "last_seen": finding.get("last_seen", ""),
            "can_ignore_device": isinstance(finding.get("device_criteria"), Mapping),
            "can_ignore_type": isinstance(finding.get("type_criteria"), Mapping),
        }
        for finding_id, finding in storage.visible_findings()
    ]
    rules = [
        {
            "id": storage.rule_id(rule),
            "name": rule["label"],
            "kind": rule["kind"],
            "integration": rule["criteria"].get("domain", ""),
        }
        for rule in storage.rules
    ]
    return {"available": True, "findings": findings, "rules": rules}


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/subscribe",
        vol.Optional("language", default="en"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_subscribe(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Send the initial view and push updates until the client unsubscribes."""
    language = msg["language"].lower().replace("_", "-").split("-", 1)[0]
    language = "de" if language == "de" else "en"
    translations = await async_get_translations(
        hass, language, "common", integrations={DOMAIN}
    )
    prefix = f"component.{DOMAIN}.common."
    texts = {
        key.removeprefix(prefix): value
        for key, value in translations.items()
        if key.startswith(prefix)
    }
    if (storage := _storage(hass)) is None:
        connection.send_error(
            msg["id"], "not_loaded", "Discovery Monitor is not loaded"
        )
        return
    runtime: PanelRuntime = hass.data[_DATA_PANEL]

    @callback
    def send_snapshot() -> None:
        connection.send_event(msg["id"], {**_snapshot(storage), "texts": texts})

    unsubscribe = storage.async_subscribe(send_snapshot)

    @callback
    def cleanup() -> None:
        unsubscribe()
        runtime.close_subscriptions.discard(close)

    @callback
    def close() -> None:
        connection.send_event(msg["id"], {"available": False, "texts": texts})
        cleanup()

    runtime.close_subscriptions.add(close)
    connection.subscriptions[msg["id"]] = cleanup
    connection.send_result(msg["id"])
    send_snapshot()


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/action",
        vol.Required("action"): vol.In(["dismiss", DEVICE, DEVICE_TYPE, "unignore"]),
        vol.Required("target"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_action(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Execute existing management actions after validating their current target."""
    if (storage := _storage(hass)) is None:
        connection.send_error(
            msg["id"], "not_loaded", "Discovery Monitor is not loaded"
        )
        return
    action, target = msg["action"], msg["target"]
    if action == "unignore":
        success = await storage.async_remove_rule(target)
    else:
        finding = storage.findings.get(target)
        if finding is None or not storage.is_finding_visible(finding):
            connection.send_error(
                msg["id"], "stale_target", "Discovery is no longer open"
            )
            return
        if action == "dismiss":
            success = await storage.async_dismiss_finding(target)
        else:
            success = await hass.data[DOMAIN].async_ignore_finding(target, action)
    if not success:
        connection.send_error(
            msg["id"], "stale_target", "This action is no longer available"
        )
        return
    connection.send_result(msg["id"])
