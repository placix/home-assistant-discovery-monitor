"""Build conservative identities for discovery data."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

DEVICE = "device"
DEVICE_TYPE = "device_type"

_MAC_RE = re.compile(r"^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$")
_DEVICE_ID_FIELDS = ("uuid", "ssdp_udn", "serial_number", "serial", "ssdp_usn")
_MODEL_FIELDS = ("model", "product_id", "pid", "product")
_MAKER_FIELDS = ("manufacturer_id", "manufacturer", "vendor_id", "vid")
_SERVICE_FIELDS = ("service_type", "service_uuids", "ssdp_nt", "ssdp_st", "type")
_MAC_HEX_LENGTH = 12


def device_criteria(
    domain: str,
    source: str,
    unique_id: str | None,
    data: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Return only a demonstrably stable device identity."""
    if value := _normalize_scalar(unique_id):
        return {"domain": domain.lower(), "unique_id": value}

    hardware = _hardware_identity(source.lower(), data)
    return {"domain": domain.lower(), **hardware} if hardware else None


def _hardware_identity(source: str, data: Mapping[str, Any]) -> dict[str, str] | None:
    """Return a source-specific hardware identity."""
    if source == "bluetooth":
        address = _normalized_mac(data.get("address"))
        address_type = _normalize_scalar(data.get("address_type"))
        if address_type and address_type not in {"public", "public_identity"}:
            return None
        # Without an explicit type, accept only a globally administered address.
        # This deliberately rejects local/private addresses and UUID-like values.
        return (
            {"bluetooth_address": address}
            if address
            and not (int(address[:2], 16) & 0x01)
            and (address_type is not None or not (int(address[:2], 16) & 0x02))
            else None
        )
    if source == "dhcp":
        for field in ("mac_address", "mac", "macaddress"):
            if (address := _normalized_mac(data.get(field))) and not (
                int(address[:2], 16) & 0x01
            ):
                return {"mac_address": address}
        return None
    if source in {"ssdp", "zeroconf", "homekit"}:
        return _service_identity(data, source)
    return None


def _service_identity(data: Mapping[str, Any], source: str) -> dict[str, str] | None:
    """Return a permanent ID advertised by an IP discovery service."""
    fields = (*_DEVICE_ID_FIELDS, "id") if source == "homekit" else _DEVICE_ID_FIELDS
    for field in fields:
        if value := _normalize_scalar(data.get(field)):
            if field == "ssdp_usn":
                value = value.split("::", 1)[0]
            return {field: value}
    return None


def type_criteria(
    domain: str, source: str, data: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Return a conservative, non-device-specific product identity."""
    result: dict[str, Any] = {"domain": domain.lower()}
    has_product_or_service = False

    for field in _MODEL_FIELDS:
        if value := _normalize_scalar(data.get(field)):
            result[field] = value
            has_product_or_service = True
    for field in _MAKER_FIELDS:
        if value := _normalize_scalar(data.get(field)):
            result[field] = value
    for field in _SERVICE_FIELDS:
        value = _normalize_value(data.get(field))
        if value is not None:
            result[field] = value
            has_product_or_service = True

    # A domain or manufacturer by itself is too broad. DHCP data commonly contains
    # only a host and an address and must therefore not generate a type rule.
    if not has_product_or_service:
        return None
    if source == "bluetooth" and not any(
        field in result for field in (*_MODEL_FIELDS, "service_uuids")
    ):
        return None
    return result


def criteria_match(
    criteria: Mapping[str, Any], candidate: Mapping[str, Any] | None
) -> bool:
    """Return whether every stored normalized feature matches."""
    return candidate is not None and dict(criteria) == dict(candidate)


def _normalized_mac(value: Any) -> str | None:
    """Normalize a MAC-like address without accepting arbitrary identifiers."""
    if not isinstance(value, str):
        return None
    compact = re.sub(r"[^0-9a-fA-F]", "", value)
    if len(compact) != _MAC_HEX_LENGTH:
        return None
    normalized = ":".join(
        compact[index : index + 2] for index in range(0, _MAC_HEX_LENGTH, 2)
    ).lower()
    if not _MAC_RE.fullmatch(normalized) or normalized in {
        "00:00:00:00:00:00",
        "ff:ff:ff:ff:ff:ff",
    }:
        return None
    return normalized


def _normalize_value(value: Any) -> str | list[str] | None:
    """Normalize a scalar or an order-independent string list."""
    if scalar := _normalize_scalar(value):
        return scalar
    if isinstance(value, (list, tuple, set, frozenset)):
        items = sorted({item for raw in value if (item := _normalize_scalar(raw))})
        return items or None
    return None


def _normalize_scalar(value: Any) -> str | None:
    """Normalize a stable comparison value."""
    if not isinstance(value, str):
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        return None
    normalized = " ".join(value.strip().lower().split())
    return normalized or None
