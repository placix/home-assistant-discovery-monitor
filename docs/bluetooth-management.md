# Bluetooth discovery management

Bluetooth addresses are not proof that two advertisements belong to the same
physical device. Discovery Monitor never joins different addresses automatically.
An address that has not been ignored or reported before remains a new discovery.

## Model rules

Recognized Bluetooth discoveries can offer an additional **Ignore all devices of
model …** action in the management panel and integration options. The confirmation
names the model and integration: the rule covers **all reliably recognized devices
of that model within that integration**, including devices with other addresses.
It does not cover an entire manufacturer or integration. Creating a model rule
requires an explicit action and confirmation; discovery never creates one.

Initial recognition is intentionally limited:

| Integration | Recognized models | Required evidence |
| --- | --- | --- |
| `victron_ble` | SmartSolar MPPT 75/15, 75/10, 100/15, 100/30, 100/50, 150/35, 100/20, 100/20 48V, 150/45, 150/60, 150/70 | Complete product advertisement with an allowlisted cleartext product ID and the solar-charger record format |
| `govee_ble` | H5105 | Exact protocol model token in the Bluetooth local name plus the supported measurement packet shape and prefix |
| `thermopro` | TP357S | Exact protocol model token in the Bluetooth local name plus the supported five-byte measurement format |

These checks use raw discovery initialization data, not Home Assistant display
names or title placeholders. Manufacturer identifiers and service UUIDs alone
never establish a model. Missing, unsupported or contradictory data means no model
action. In particular, the eight-byte Govee message format is not treated as H5105.
Old findings without this evidence gain no model action until recognized again.
For real HA Bluetooth objects the public advertisement's `local_name` is used;
a cached `BLEDevice.name` fallback cannot substitute for a missing local name.

Model rules use a separate `model` kind and exact versioned criteria:
`domain`, `profile: bluetooth_model`, `version: 1`, and `model`.
Existing `device` and `device_type` rules retain their criteria and matching
semantics. They are neither migrated nor broadened. Optional RSSI, service UUIDs,
measurement values and changing address suffixes are not model criteria.

An explicit ignore action still removes that discovery's current flow; future
flows matching the rule are ignored as before. Removing the rule allows matching
future discoveries again. Missing model evidence never triggers an approximate
match or a manufacturer-wide fallback.

## Optional notification pauses

Open the integration options and choose **Notifications and diagnostics**:

- **Pause between notifications of the same discovery:** `0` hours disables
  suppression (default). Choose 1–720 hours to enable a bounded pause.
- **Reset stored notification pauses:** forget the event timestamps without
  modifying findings, counters or ignore rules.
- **Explain discovery decisions in debug logs:** enable the additional diagnostics.

YAML-only configurations can set the same duration without creating a UI entry:

```yaml
discovery_monitor:
  notification_pause_hours: 24
  debug_logging: false
```

UI changes take effect without reloading the monitor and preserve YAML filters.
For Bluetooth, a pause is keyed to the same domain and MAC-valued HA unique ID;
another address is never merged based on name, model, manufacturer or signal
strength. Explicitly private/rotating or ambiguous address types are excluded.
When HA does not expose an address type, the key denotes the exact HA discovery
address for a limited period, not a proven permanent physical identity. Ethernet
MAC-bit heuristics are not used to infer Bluetooth address types.

The period starts at the last actually emitted event. Suppressed repeats update
history and counters but do not extend the period, create ignore rules, hide the
finding, or abort its HA flow. The next discovery after expiry can emit again.
Turning the pause off always permits events; actual emission timestamps continue
to be tracked so re-enabling it uses the last real event.

Event timestamps are stored separately from the 100-finding history using HA
Store. Writes are coalesced and flushed on normal HA shutdown. The store retains
at most 1,000 hashed identities for 30 days; eviction can permit another event,
never suppress a new identity. An abrupt crash before a delayed write may allow
one extra notification. Ignore-rule changes still use immediate HA Store saves.

The `discovery_monitor_discovered` payload is unchanged. Existing Telegram
automations need no changes. HA's own ignored config entries, Discovery Monitor
ignore rules, hidden findings and notification pauses remain separate mechanisms.

## Diagnostics

Enable `debug_logging` and set the logger level to DEBUG, for example:

```yaml
logger:
  logs:
    custom_components.discovery_monitor: debug
```

Decision messages contain the integration/source, identity source and hashed
correlation reference, recognized model or recognition failure, checked rule
types/counts and the event/ignore/pause decision. They exclude raw addresses,
unique IDs, flow IDs, arbitrary device names, keys and advertisement contents.
Unknown identity or unsupported model evidence is reported without dumping it.
Missing manufacturer data, missing model names/data, truncated advertisements,
unsupported model tokens and unsupported packet formats have distinct reasons.

## Extending recognition and validation

`bluetooth_models.py` contains independent domain profiles and a trusted model
catalog. Add a profile only with documented raw discovery evidence and positive,
negative and incomplete-packet tests. Do not reinterpret existing version-1
criteria when adding a new format.

Protocol references used for the initial profiles:

- [Victron product advertisement parsing](https://github.com/keshavdv/victron-ble/blob/v0.9.3/victron_ble/devices/base.py)
- [Victron model identifiers](https://github.com/keshavdv/victron-ble/blob/v0.9.3/victron_ble/devices/__init__.py)
- [Govee parser and packet formats](https://github.com/Bluetooth-Devices/govee-ble/blob/v1.4.0/src/govee_ble/parser.py)
- [ThermoPro parser and packet formats](https://github.com/Bluetooth-Devices/thermopro-ble/blob/v1.1.4/src/thermopro_ble/parser.py)

The full pytest suite uses HA 2026.10.4 with its matching fixture package
`pytest-homeassistant-custom-component==0.13.375`. That fixture package does not
support HA 2026.11 and must not be silently downgraded by dependency resolution.

The validation workflow additionally creates a separate HA 2026.11.2 runtime and
runs `python -m tests.ha_compatibility`. This uses real HA flow callbacks, event bus
and storage for repeat notifications, resetting/reloading pauses, explicit model
rules, new addresses, incomplete discovery data and listener cleanup. Only the
Bluetooth integration handler's confirmation step is substituted; it does not
simulate a radio or establish which physical devices sent real-world addresses.
The test installs Bluetooth import dependencies from HA 2026.11.2's own manifests
and constructs real `BluetoothServiceInfoBleak` objects via `from_scan`.
Reset tests overlap pending/in-flight writes, concurrent resets and new events,
then read the resulting file and load it in a fresh Home Assistant instance.
The compatibility test complements the complete pytest suite; it is not a full
HA 2026.11 end-to-end integration or hardware test.

Browser regression tests: `npm ci --prefix tests/frontend`, install Chromium with
Playwright, then `npm test --prefix tests/frontend`.
