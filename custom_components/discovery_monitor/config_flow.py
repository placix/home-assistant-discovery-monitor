"""Config and options flows for Discovery Monitor."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

from .const import DOMAIN
from .identity import DEVICE


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Create the single UI entry used to expose options."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Create a singleton entry without unnecessary setup questions."""
        self._async_abort_entries_match()
        if user_input is not None:
            return self.async_create_entry(title="Discovery Monitor", data={})
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(
        _config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the ignored-item management flow."""
        return DiscoveryOptionsFlow()


class DiscoveryOptionsFlow(config_entries.OptionsFlow):
    """Show ignore rules and remove one on request."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """List ignored devices and types using human-readable labels."""
        monitor = self.hass.data.get(DOMAIN)
        storage = getattr(monitor, "storage", None)
        rules = storage.rules if storage is not None else []

        if user_input is not None:
            rule_id = user_input.get("ignored_item")
            if storage is not None and isinstance(rule_id, str):
                await storage.async_remove_rule(rule_id)
            return self.async_create_entry(data=dict(self.config_entry.options))

        choices = {
            storage.rule_id(rule): (
                f"{rule['label']} — "
                f"{'Gerät' if rule['kind'] == DEVICE else 'Gerätetyp'}"
            )
            for rule in rules
        } if storage is not None else {}
        schema = (
            vol.Schema({vol.Required("ignored_item"): vol.In(choices)})
            if choices
            else vol.Schema({})
        )
        return self.async_show_form(step_id="init", data_schema=schema)
