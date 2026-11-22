"""Small, opt-in model profiles; never infer a physical device across addresses.

Profiles use the advertisement formats exercised by govee-ble 1.4.0,
thermopro-ble 1.1.4 and victron-ble 0.9.3. No parser imports, radio I/O,
decryption or access to another integration's private parser state is needed.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

MODEL = "model"
MODEL_VERSION = 1
_VICTRON_MIN_LENGTH = 20
_VICTRON_PRODUCT_ADVERTISEMENT = 0x10
_MANUFACTURER_KEY_MAX = 0xFFFF
_THERMOPRO_PAYLOAD_LENGTH = 5
_VICTRON_MODELS = {
    "a053": "SmartSolar MPPT 75/15",
    "a054": "SmartSolar MPPT 75/10",
    "a055": "SmartSolar MPPT 100/15",
    "a056": "SmartSolar MPPT 100/30",
    "a057": "SmartSolar MPPT 100/50",
    "a058": "SmartSolar MPPT 150/35",
    "a05f": "SmartSolar MPPT 100/20",
    "a060": "SmartSolar MPPT 100/20 48V",
    "a061": "SmartSolar MPPT 150/45",
    "a062": "SmartSolar MPPT 150/60",
    "a063": "SmartSolar MPPT 150/70",
}
_LABELS = {
    "victron_ble": _VICTRON_MODELS,
    "govee_ble": {"h5105": "Govee H5105"},
    "thermopro": {"tp357s": "ThermoPro TP357S"},
}
_GOVEE_NAME = re.compile(r"(?:GVH5105|Govee_H5105)_[0-9a-fA-F]{4}")
_THERMOPRO_NAME = re.compile(r"TP357S(?: \([0-9a-fA-F]{4}\))?")


@dataclass(frozen=True)
class ModelDetection:
    """A versioned model identity, display label and diagnostic outcome."""

    criteria: dict[str, Any] | None
    label: str | None
    reason: str


def model_label(criteria: Any) -> str | None:
    """Validate a profile and return its trusted display name."""
    if (
        not isinstance(criteria, Mapping)
        or set(criteria) != {"domain", "profile", "version", "model"}
        or type(criteria.get("version")) is not int
        or criteria["version"] != MODEL_VERSION
        or criteria.get("profile") != "bluetooth_model"
        or not isinstance(criteria.get("domain"), str)
        or not isinstance(criteria.get("model"), str)
    ):
        return None
    return _LABELS.get(criteria["domain"], {}).get(criteria["model"])


def detect_model(domain: str, source: str, info: Any) -> ModelDetection:
    """Recognize only complete advertisements from explicitly supported profiles."""
    if source != "bluetooth" or domain not in _PROFILES:
        return ModelDetection(None, None, "no_profile")
    try:
        name = (
            info.get("name")
            if isinstance(info, Mapping)
            else getattr(info, "name", None)
        )
        data = (
            info.get("manufacturer_data")
            if isinstance(info, Mapping)
            else getattr(info, "manufacturer_data", None)
        )
        if not isinstance(data, Mapping):
            return ModelDetection(None, None, "missing_manufacturer_data")
        model = _PROFILES[domain](name, data)
    except AttributeError, TypeError, ValueError:
        return ModelDetection(None, None, "invalid_advertisement")
    if model is None:
        return ModelDetection(None, None, "unsupported_or_incomplete_model")
    criteria = {
        "domain": domain,
        "profile": "bluetooth_model",
        "version": MODEL_VERSION,
        "model": model,
    }
    return ModelDetection(criteria, model_label(criteria), "model_recognized")


def _victron(_name: Any, data: Mapping[Any, Any]) -> str | None:
    """Use the cleartext product ID, never the generic Victron record type."""
    packet = data.get(0x02E1)
    # Product Advertisement, known exact model, Solar Charger record with at
    # least the mandatory payload. Nonce, key-check byte and ciphertext ignored.
    if (
        not isinstance(packet, bytes)
        or len(packet) < _VICTRON_MIN_LENGTH
        or packet[0] != _VICTRON_PRODUCT_ADVERTISEMENT
    ):
        return None
    model = f"{int.from_bytes(packet[2:4], 'little'):04x}"
    return model if packet[4] == 1 and model in _VICTRON_MODELS else None


def _govee(name: Any, data: Mapping[Any, Any]) -> str | None:
    """Combine the protocol model token with its documented packet shape."""
    if not isinstance(name, str) or _GOVEE_NAME.fullmatch(name) is None:
        return None
    packet = data.get(1)
    if not isinstance(packet, bytes):
        return None
    # The upstream parser strips this 25-byte appended beacon from some frames.
    # Eight-byte messages belong to a different model and must not match.
    if len(packet) in (31, 32) and b"INTELLI_ROCKS" in packet[-25:]:
        packet = packet[:-25]
    if len(packet) in (6, 7) and packet[:2] == b"\x01\x01":
        return "h5105"
    return None


def _thermopro(name: Any, data: Mapping[Any, Any]) -> str | None:
    """Require an exact protocol model token and the TP357S measurement format."""
    if not isinstance(name, str) or _THERMOPRO_NAME.fullmatch(name) is None:
        return None
    # ThermoPro encodes measurement bytes in the manufacturer key itself.
    # It is NOT a stable manufacturer identity. Multiple cached packets are OK.
    if data and all(
        type(key) is int
        and 0 <= key <= _MANUFACTURER_KEY_MAX
        and isinstance(value, bytes)
        and len(value) == _THERMOPRO_PAYLOAD_LENGTH
        for key, value in data.items()
    ):
        return "tp357s"
    return None


_PROFILES: dict[str, Callable[[Any, Mapping[Any, Any]], str | None]] = {
    "victron_ble": _victron,
    "govee_ble": _govee,
    "thermopro": _thermopro,
}
