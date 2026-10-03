"""Observe Home Assistant discovery config flows."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from ipaddress import IPv4Address, IPv6Address
from typing import TYPE_CHECKING, Any

from homeassistant import config_entries
from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.data_entry_flow import UnknownFlow

from .const import (
    CONF_DEBUG_LOGGING,
    CONF_ENABLED,
    CONF_EXCLUDE_DOMAINS,
    CONF_EXCLUDE_SOURCES,
    CONF_INCLUDE_DISCOVERY_DATA,
    CONF_INCLUDE_DOMAINS,
    CONF_INCLUDE_SOURCES,
    DEFAULT_OPTIONS,
    EVENT_DISCOVERED,
)
from .identity import device_criteria, type_criteria

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .storage import DiscoveryStore

_LOGGER = logging.getLogger(__name__)

_FALLBACK_DISCOVERY_SOURCES = frozenset(
    {
        "bluetooth",
        "dhcp",
        "discovery",
        "esphome",
        "hardware",
        "hassio",
        "homekit",
        "import",
        "integration_discovery",
        "mqtt",
        "ssdp",
        "system",
        "usb",
        "zeroconf",
    }
)
_SAFE_FIELDS = frozenset(
    {
        "address",
        "address_type",
        "connectable",
        "description",
        "device",
        "device_class",
        "host",
        "hostname",
        "id",
        "ip",
        "ip_address",
        "mac",
        "mac_address",
        "macaddress",
        "manufacturer",
        "manufacturer_id",
        "model",
        "name",
        "pid",
        "port",
        "product",
        "product_id",
        "rssi",
        "serial",
        "serial_number",
        "service_type",
        "service_uuids",
        "source",
        "source_ip",
        "ssdp_location",
        "ssdp_nt",
        "ssdp_server",
        "ssdp_st",
        "ssdp_udn",
        "ssdp_usn",
        "topic",
        "tx_power",
        "type",
        "uuid",
        "vendor_id",
        "vid",
    }
)
_SECRET_MARKERS = (
    "api_key",
    "apikey",
    "auth",
    "cookie",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
)
_MAX_SEEN_FLOW_IDS = 512


class FlowDataAdapter:
    """Isolate the optional access to HA's internal in-progress flow storage."""

    def __init__(self, flow_manager: Any) -> None:
        """Initialize the adapter."""
        self._flow_manager = flow_manager

    @callback
    def async_get_init_data(self, flow_id: str) -> Any | None:
        """Return initial discovery data, or None if HA internals changed."""
        try:
            progress = getattr(self._flow_manager, "_progress", None)
            if not isinstance(progress, Mapping):
                return None
            flow = progress.get(flow_id)
            return getattr(flow, "init_data", None)
        except Exception:  # noqa: BLE001 - compatibility boundary for HA internals
            return None

    @callback
    def async_get_description_placeholders(self, flow_id: str) -> Mapping[str, Any]:
        """Return safe access to current-step placeholders when available."""
        try:
            progress = getattr(self._flow_manager, "_progress", None)
            flow = progress.get(flow_id) if isinstance(progress, Mapping) else None
            current_step = getattr(flow, "cur_step", None)
            if not isinstance(current_step, Mapping):
                return {}
            placeholders = current_step.get("description_placeholders", {})
            return placeholders if isinstance(placeholders, Mapping) else {}
        except Exception:  # noqa: BLE001 - compatibility boundary for HA internals
            return {}


class DiscoveryMonitor:
    """Watch newly initialized, user-visible discovery flows."""

    def __init__(
        self,
        hass: HomeAssistant,
        options: Mapping[str, Any],
        storage: DiscoveryStore | None = None,
    ) -> None:
        """Initialize the monitor."""
        self._hass = hass
        self._options = options
        self.storage = storage
        self._unsubscribe: CALLBACK_TYPE | None = None
        self._adapter = FlowDataAdapter(hass.config_entries.flow)
        self._seen_flow_ids: set[str] = set()
        self._seen_order: deque[str] = deque()

    @callback
    def async_start(self) -> None:
        """Start monitoring if the supported HA callback exists."""
        subscribe = getattr(
            self._hass.config_entries.flow, "async_subscribe_flow", None
        )
        if not callable(subscribe):
            _LOGGER.warning(
                "Discovery flow monitoring is unavailable: this Home Assistant "
                "version has no async_subscribe_flow API"
            )
            return
        try:
            self._unsubscribe = subscribe(self._async_flow_changed)
        except Exception:
            _LOGGER.exception("Unable to subscribe to Home Assistant discovery flows")

    @callback
    def async_stop(self) -> None:
        """Stop monitoring. Safe to call more than once."""
        if self._unsubscribe is None:
            return
        unsubscribe, self._unsubscribe = self._unsubscribe, None
        try:
            unsubscribe()
        except Exception:  # noqa: BLE001 - unload must remain safe across HA changes
            _LOGGER.debug("Discovery flow listener was already removed")

    async def async_ignore_finding(self, finding_id: str, kind: str) -> bool:
        """Persist an ignore rule and abort its current flow when still active."""
        if self.storage is None or not await self.storage.async_add_rule(
            finding_id, kind
        ):
            return False

        if flow_id := self.storage.active_flow_id(finding_id):
            try:
                self._hass.config_entries.flow.async_abort(flow_id)
            except AttributeError, UnknownFlow, KeyError, TypeError:
                _LOGGER.debug(
                    "Discovery flow %s disappeared before it could be ignored",
                    flow_id,
                )
        return True

    @callback
    def _async_flow_changed(self, change: str, flow_id: str) -> None:  # noqa: C901, PLR0911
        """Handle an added discovery flow."""
        if change != "added" or self._already_seen(flow_id):
            return

        options = DEFAULT_OPTIONS | dict(self._options)
        if not options[CONF_ENABLED]:
            return

        try:
            flow = self._hass.config_entries.flow.async_get(flow_id)
        except AttributeError, UnknownFlow, KeyError, TypeError:
            _LOGGER.debug("Discovery flow %s disappeared before inspection", flow_id)
            return

        context = flow.get("context")
        if not isinstance(context, Mapping):
            return
        domain = _as_string(flow.get("handler"))
        source = _as_string(context.get("source"))
        if domain is None or source not in _discovery_sources():
            return
        init_data = self._adapter.async_get_init_data(flow_id)
        discovery_data = extract_safe_discovery_data(init_data)
        placeholders = {
            **_safe_string_mapping(context.get("title_placeholders")),
            **_safe_string_mapping(
                self._adapter.async_get_description_placeholders(flow_id)
            ),
        }
        device_name = _first_string(
            discovery_data.get("name"),
            discovery_data.get("hostname"),
            placeholders.get("name"),
        )
        title = _first_string(
            placeholders.get("name"),
            placeholders.get("title"),
            device_name,
        )
        address = _first_string(
            discovery_data.get("address"),
            discovery_data.get("mac_address"),
            discovery_data.get("mac"),
            discovery_data.get("macaddress"),
            discovery_data.get("host"),
            discovery_data.get("ip_address"),
            discovery_data.get("ip"),
        )
        unique_id = _as_string(context.get("unique_id"))
        device = device_criteria(domain, source, unique_id, discovery_data)
        device_type = type_criteria(domain, source, discovery_data)
        label = _first_string(title, device_name, domain) or domain

        if self.storage is not None and self.storage.matching_rule(device, device_type):
            self._schedule_record(
                flow_id,
                domain,
                source,
                label,
                device,
                device_type,
                discovery_data,
            )
            try:
                self._hass.config_entries.flow.async_abort(flow_id)
            except AttributeError, UnknownFlow, KeyError, TypeError:
                _LOGGER.debug(
                    "Ignored discovery flow %s disappeared before it could be aborted",
                    flow_id,
                )
            _LOGGER.info("Suppressed an ignored discovery for %s", label)
            return

        if not _matches_filters(domain, source, options):
            return

        event_data: dict[str, Any] = {
            "domain": domain,
            "source": source,
            "title": title,
            "unique_id": unique_id,
            "flow_id": flow_id,
            "device_name": device_name,
            "address": address,
        }
        if options[CONF_INCLUDE_DISCOVERY_DATA]:
            event_data["discovery_data"] = discovery_data

        self._schedule_record(
            flow_id,
            domain,
            source,
            label,
            device,
            device_type,
            discovery_data,
        )
        self._hass.bus.async_fire(EVENT_DISCOVERED, event_data)
        if options[CONF_DEBUG_LOGGING]:
            _LOGGER.debug(
                "Reported discovery flow %s for %s from %s",
                flow_id,
                domain,
                source,
            )

    def _schedule_record(  # noqa: PLR0913, PLR0917
        self,
        flow_id: str,
        domain: str,
        source: str,
        label: str,
        device: Mapping[str, Any] | None,
        device_type: Mapping[str, Any] | None,
        discovery_data: Mapping[str, Any],
    ) -> None:
        """Persist a finding without delaying the flow callback."""
        if self.storage is None:
            return
        fallback = {
            key: value
            for key, value in discovery_data.items()
            if key
            not in {
                "host",
                "ip",
                "ip_address",
                "port",
                "rssi",
                "source_ip",
                "ssdp_location",
                "tx_power",
            }
        }

        async def async_record() -> None:
            await self.storage.async_record(
                domain=domain,
                source=source,
                label=label,
                device=device,
                device_type=device_type,
                fallback=fallback,
                flow_id=flow_id,
            )

        create_task = getattr(self._hass, "async_create_task", None)
        if callable(create_task):
            create_task(async_record(), f"Record discovery of {label}")
            return

        # Lightweight unit-test hosts do not implement HomeAssistant's task helper.
        asyncio.get_running_loop().create_task(async_record())

    @callback
    def _already_seen(self, flow_id: str) -> bool:
        """Return whether a callback was already handled and bound memory use."""
        if flow_id in self._seen_flow_ids:
            return True
        self._seen_flow_ids.add(flow_id)
        self._seen_order.append(flow_id)
        if len(self._seen_order) > _MAX_SEEN_FLOW_IDS:
            self._seen_flow_ids.remove(self._seen_order.popleft())
        return False


def _discovery_sources() -> frozenset[str]:
    """Return HA's current discovery source set with a defensive fallback."""
    sources = getattr(config_entries, "DISCOVERY_SOURCES", None)
    if isinstance(sources, (set, frozenset)):
        return frozenset(str(source) for source in sources)
    return _FALLBACK_DISCOVERY_SOURCES


def _parse_list(value: Any) -> set[str]:
    """Parse comma/newline separated option values."""
    if not isinstance(value, str):
        return set()
    return {
        item.strip().lower()
        for line in value.splitlines()
        for item in line.split(",")
        if item.strip()
    }


def _matches_filters(domain: str, source: str, options: Mapping[str, Any]) -> bool:
    """Apply inclusive and exclusive source/domain filters."""
    include_sources = _parse_list(options.get(CONF_INCLUDE_SOURCES))
    exclude_sources = _parse_list(options.get(CONF_EXCLUDE_SOURCES))
    include_domains = _parse_list(options.get(CONF_INCLUDE_DOMAINS))
    exclude_domains = _parse_list(options.get(CONF_EXCLUDE_DOMAINS))
    return not (
        (include_sources and source not in include_sources)
        or source in exclude_sources
        or (include_domains and domain not in include_domains)
        or domain in exclude_domains
    )


def extract_safe_discovery_data(data: Any) -> dict[str, Any]:
    """Extract only explicitly allowed, JSON-safe fields from discovery data."""
    if data is None:
        return {}
    values: dict[str, Any] = {}
    if isinstance(data, Mapping):
        candidates = data.items()
    elif is_dataclass(data) and not isinstance(data, type):
        candidates = ((field.name, getattr(data, field.name)) for field in fields(data))
    else:
        candidates = _safe_object_attributes(data)

    for raw_key, value in candidates:
        key = str(raw_key).lower()
        if key not in _SAFE_FIELDS or _is_secret_key(key):
            continue
        safe_value = _json_safe_value(value)
        if safe_value is not None:
            values[key] = safe_value
    return values


def _json_safe_value(value: Any) -> Any | None:
    """Convert a small set of scalar/list values without dumping objects."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (IPv4Address, IPv6Address)):
        return str(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        safe_items = [
            item
            for item in (_json_safe_value(item) for item in value)
            if item is not None
        ]
        return safe_items[:64]
    return None


def _safe_object_attributes(data: Any) -> list[tuple[str, Any]]:
    """Read only allowlisted attributes from slot/property-based service info."""
    values: list[tuple[str, Any]] = []
    for field_name in _SAFE_FIELDS:
        try:
            value = getattr(data, field_name)
        except AttributeError:
            continue
        except Exception:
            _LOGGER.debug(
                "Unable to read safe discovery field %s", field_name, exc_info=True
            )
            continue
        values.append((field_name, value))
    return values


def _is_secret_key(key: str) -> bool:
    """Return whether a field name looks credential-bearing."""
    return any(marker in key for marker in _SECRET_MARKERS)


def _safe_string_mapping(value: Any) -> dict[str, str]:
    """Return non-secret string placeholders only."""
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): item
        for key, item in value.items()
        if isinstance(item, str) and not _is_secret_key(str(key).lower())
    }


def _as_string(value: Any) -> str | None:
    """Return a string value without coercing arbitrary objects."""
    return value if isinstance(value, str) and value else None


def _first_string(*values: Any) -> str | None:
    """Return the first non-empty string."""
    return next((value for value in values if isinstance(value, str) and value), None)
