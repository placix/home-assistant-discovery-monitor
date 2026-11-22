"""Config and options flows for Discovery Monitor."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from homeassistant.helpers.translation import async_get_translations

from .bluetooth_models import MODEL, model_label
from .const import CONF_DEBUG_LOGGING, CONF_NOTIFICATION_PAUSE_HOURS, DOMAIN
from .identity import DEVICE, DEVICE_TYPE
from .notifications import MAX_PAUSE_HOURS

_DISMISS = "dismiss"

if TYPE_CHECKING:
    from .monitor import DiscoveryMonitor
    from .storage import DiscoveryStore


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
        """Return the central discovery management flow."""
        return DiscoveryOptionsFlow()


class DiscoveryOptionsFlow(config_entries.OptionsFlow):
    """Manage recent discoveries and ignore rules."""

    def __init__(self) -> None:
        """Initialize the options flow."""
        self._selected_finding_id: str | None = None
        self._confirmed_model: str | None = None

    @property
    def _monitor(self) -> DiscoveryMonitor | None:
        """Return the active monitor when available."""
        monitor = self.hass.data.get(DOMAIN)
        return monitor if monitor is not None else None

    @property
    def _storage(self) -> DiscoveryStore | None:
        """Return persistent Discovery Monitor storage when available."""
        monitor = self._monitor
        storage = getattr(monitor, "storage", None)
        return storage if storage is not None else None

    async def async_step_init(
        self, _user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Show management areas and optional notification settings."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["recent_findings", "ignored_items", "notifications"],
        )

    async def async_step_recent_findings(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Select a recent discovery without exposing technical identifiers."""
        storage = self._storage
        visible_findings = storage.visible_findings() if storage is not None else []
        if user_input is not None:
            if isinstance(finding_id := user_input.get("finding"), str):
                self._selected_finding_id = finding_id
                return await self.async_step_finding_action()
            if not visible_findings:
                return self.async_create_entry(data=dict(self.config_entry.options))

        texts = await self._async_dynamic_texts() if visible_findings else {}
        choices = [
            {
                "value": finding_id,
                "label": _finding_label(finding, texts),
            }
            for finding_id, finding in visible_findings
        ]
        schema = (
            vol.Schema(
                {
                    vol.Required("finding"): SelectSelector(
                        SelectSelectorConfig(
                            options=choices,
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            )
            if choices
            else vol.Schema({})
        )
        return self.async_show_form(
            step_id="recent_findings",
            data_schema=schema,
        )

    async def async_step_finding_action(  # noqa: C901, PLR0911
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Offer only ignore actions backed by stable discovery criteria."""
        storage = self._storage
        finding = (
            storage.findings.get(self._selected_finding_id)
            if storage is not None and self._selected_finding_id is not None
            else None
        )
        if not isinstance(finding, Mapping):
            return self.async_abort(reason="identity_unavailable")

        texts = await self._async_dynamic_texts()
        choices: dict[str, str] = {_DISMISS: texts["dismiss_finding"]}
        if isinstance(finding.get("device_criteria"), Mapping):
            choices[DEVICE] = texts["ignore_device"]
        if isinstance(finding.get("type_criteria"), Mapping):
            choices[DEVICE_TYPE] = texts["ignore_device_type"]
        if model := model_label(finding.get("model_criteria")):
            choices[MODEL] = texts["ignore_model"].format(model=model)

        if user_input is not None and isinstance(
            action := user_input.get("action"), str
        ):
            if action not in choices:
                return self.async_abort(reason="identity_unavailable")
            if action == MODEL:
                return await self.async_step_confirm_model()
            if action == _DISMISS:
                if (
                    storage is None
                    or self._selected_finding_id is None
                    or not await storage.async_dismiss_finding(
                        self._selected_finding_id
                    )
                ):
                    return self.async_abort(reason="identity_unavailable")
            else:
                monitor = self._monitor
                if (
                    monitor is None
                    or self._selected_finding_id is None
                    or not await monitor.async_ignore_finding(
                        self._selected_finding_id, action
                    )
                ):
                    return self.async_abort(reason="identity_unavailable")
            self._selected_finding_id = None
            return await self.async_step_recent_findings()

        return self.async_show_form(
            step_id="finding_action",
            data_schema=vol.Schema({vol.Required("action"): vol.In(choices)}),
            description_placeholders={"name": str(finding.get("label", ""))},
        )

    async def async_step_confirm_model(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Require explicit confirmation of a rule covering an entire model."""
        storage = self._storage
        finding = (
            storage.findings.get(self._selected_finding_id, {})
            if storage is not None
            else {}
        )
        model = model_label(finding.get("model_criteria"))
        if model is None:
            return self.async_abort(reason="identity_unavailable")
        if user_input is not None:
            if model != self._confirmed_model:
                return self.async_abort(reason="identity_unavailable")
            monitor = self._monitor
            if (
                monitor is None
                or self._selected_finding_id is None
                or not await monitor.async_ignore_finding(
                    self._selected_finding_id, MODEL
                )
            ):
                return self.async_abort(reason="identity_unavailable")
            self._selected_finding_id = None
            self._confirmed_model = None
            return await self.async_step_recent_findings()
        self._confirmed_model = model
        return self.async_show_form(
            step_id="confirm_model",
            data_schema=vol.Schema({}),
            description_placeholders={
                "model": model,
                "integration": str(finding.get("domain", "")),
            },
        )

    async def async_step_notifications(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Configure bounded pauses separately from permanent ignore rules."""
        monitor = self._monitor
        if monitor is None:
            return self.async_abort(reason="identity_unavailable")
        if user_input is not None:
            if user_input.get("reset_pauses") and monitor.notifications is not None:
                await monitor.notifications.async_reset()
            options = dict(self.config_entry.options) | {
                key: user_input[key]
                for key in (CONF_NOTIFICATION_PAUSE_HOURS, CONF_DEBUG_LOGGING)
            }
            return self.async_create_entry(data=options)
        options = monitor.options
        return self.async_show_form(
            step_id="notifications",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_NOTIFICATION_PAUSE_HOURS,
                        default=options[CONF_NOTIFICATION_PAUSE_HOURS],
                    ): vol.All(vol.Coerce(int), vol.Range(min=0, max=MAX_PAUSE_HOURS)),
                    vol.Required(
                        CONF_DEBUG_LOGGING, default=options[CONF_DEBUG_LOGGING]
                    ): bool,
                    vol.Optional("reset_pauses", default=False): bool,
                }
            ),
        )

    async def async_step_ignored_items(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Remove an existing ignore rule without changing discovery history."""
        storage = self._storage
        rules = storage.rules if storage is not None else []

        if user_input is not None:
            rule_id = user_input.get("ignored_item")
            if (
                storage is not None
                and isinstance(rule_id, str)
                and await storage.async_remove_rule(rule_id)
            ):
                return await self.async_step_ignored_items()
            if not rules:
                return self.async_create_entry(data=dict(self.config_entry.options))

        texts = await self._async_dynamic_texts() if rules else {}
        choices = (
            [
                {
                    "value": storage.rule_id(rule),
                    "label": _rule_label(rule, texts),
                }
                for rule in rules
            ]
            if storage is not None
            else []
        )
        schema = (
            vol.Schema(
                {
                    vol.Required("ignored_item"): SelectSelector(
                        SelectSelectorConfig(
                            options=choices,
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            )
            if choices
            else vol.Schema({})
        )
        return self.async_show_form(step_id="ignored_items", data_schema=schema)

    async def _async_dynamic_texts(self) -> dict[str, str]:
        """Return translated text used inside dynamically generated labels."""
        configured_language = getattr(
            getattr(self.hass, "config", None), "language", "en"
        )
        language = _supported_language(configured_language)
        translations = await async_get_translations(
            self.hass,
            language,
            "common",
            integrations={DOMAIN},
        )
        prefix = f"component.{DOMAIN}.common."
        return {
            key: translations[f"{prefix}{key}"]
            for key in (
                "device",
                "device_type",
                "model",
                "model_scope",
                "ignore_model",
                "integration",
                "source",
                "discoveries",
                "last_seen",
                "dismiss_finding",
                "ignore_device",
                "ignore_device_type",
                "finding",
                "unknown",
            )
        }


def _finding_label(finding: Mapping[str, Any], texts: Mapping[str, str]) -> str:
    """Return a readable choice label without technical IDs or fingerprints."""
    label = str(finding.get("label") or finding.get("domain") or texts["finding"])
    domain = str(finding.get("domain") or texts["unknown"])
    source = str(finding.get("source") or texts["unknown"])
    count = max(int(finding.get("count", 1)), 1)
    last_seen = str(finding.get("last_seen") or texts["unknown"])
    return (
        f"{label} — {texts['integration']}: {domain} · "
        f"{texts['source']}: {source} · {texts['discoveries']}: {count} · "
        f"{texts['last_seen']}: {last_seen}"
    )


def _rule_label(rule: Mapping[str, Any], texts: Mapping[str, str]) -> str:
    """Return a readable ignore-rule label without exposing its identity."""
    label = str(rule.get("label") or texts["device"])
    kind = texts.get(str(rule.get("kind")), texts["device_type"])
    criteria = rule.get("criteria")
    domain = criteria.get("domain") if isinstance(criteria, Mapping) else None
    integration = f" · {texts['integration']}: {domain}" if domain else ""
    scope = f" · {texts['model_scope']}" if rule.get("kind") == MODEL else ""
    return f"{label} — {kind}{integration}{scope}"


def _supported_language(value: Any) -> str:
    """Return German when configured, otherwise use the English fallback."""
    if not isinstance(value, str):
        return "en"
    language = value.casefold().replace("_", "-").split("-", 1)[0]
    return "de" if language == "de" else "en"
