"""Repair flow offering safe ignore actions for a discovery."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
from .identity import DEVICE, DEVICE_TYPE


@callback
def async_create_discovery_issue(
    hass: HomeAssistant,
    finding_id: str,
    label: str,
    *,
    can_ignore_device: bool,
    can_ignore_type: bool,
) -> None:
    """Expose safe choices in Home Assistant's repair UI."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        finding_id,
        data={
            "finding_id": finding_id,
            "can_ignore_device": "1" if can_ignore_device else "0",
            "can_ignore_type": "1" if can_ignore_type else "0",
        },
        is_fixable=True,
        is_persistent=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key="discovery_found",
        translation_placeholders={"name": label},
    )


async def async_create_fix_flow(
    _hass: HomeAssistant,
    _issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create the choice flow for one discovery."""
    return DiscoveryRepairFlow(data or {})


class DiscoveryRepairFlow(RepairsFlow):
    """Let the user create one of the identities proven safe for this finding."""

    def __init__(self, data: dict[str, Any]) -> None:
        """Initialize the repair flow."""
        self._data = data

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """Offer only actions backed by stable criteria."""
        choices: dict[str, str] = {}
        if self._data.get("can_ignore_device") == "1":
            choices[DEVICE] = "Dieses Gerät zukünftig ignorieren"
        if self._data.get("can_ignore_type") == "1":
            choices[DEVICE_TYPE] = "Diesen Gerätetyp zukünftig ignorieren"

        if user_input is not None:
            monitor = self.hass.data.get(DOMAIN)
            storage = getattr(monitor, "storage", None)
            finding_id = self._data.get("finding_id")
            if (
                storage is not None
                and isinstance(finding_id, str)
                and await storage.async_add_rule(finding_id, user_input["action"])
            ):
                return self.async_create_entry(data={})
            return self.async_abort(reason="identity_unavailable")

        if not choices:
            return self.async_abort(reason="identity_unavailable")
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({vol.Required("action"): vol.In(choices)}),
        )
