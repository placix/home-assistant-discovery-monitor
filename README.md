# Home Assistant Discovery Monitor

[![Validate](https://github.com/placix/home-assistant-discovery-monitor/actions/workflows/validate.yml/badge.svg)](https://github.com/placix/home-assistant-discovery-monitor/actions/workflows/validate.yml)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://www.hacs.xyz/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Discovery Monitor is a Home Assistant custom integration that exposes useful,
sanitized details about newly discovered integrations as an event.

Home Assistant's public `config_entry_discovered` event intentionally carries no
useful payload. Discovery Monitor observes the corresponding user-visible config
flows and fires `discovery_monitor_discovered` with the information Home Assistant
still has at that point.

## Event example

```yaml
event_type: discovery_monitor_discovered
event_data:
  domain: led_ble
  source: bluetooth
  title: QHM-B051
  unique_id: AA:BB:CC:DD:EE:FF
  flow_id: 01K...
  device_name: QHM-B051
  address: AA:BB:CC:DD:EE:FF
  discovery_data:
    name: QHM-B051
    address: AA:BB:CC:DD:EE:FF
```

Fields unavailable from a particular discovery source are `null`. The
`discovery_data` mapping contains only explicitly allowed, JSON-safe fields.

## Installation

### HACS custom repository

1. In HACS, open **Integrations**, then the menu and **Custom repositories**.
2. Add `https://github.com/placix/home-assistant-discovery-monitor` as an
   **Integration** repository.
3. Install **Discovery Monitor**.
4. Restart Home Assistant.
5. Add **Discovery Monitor** under **Settings → Devices & services**.

### Manual

Copy `custom_components/discovery_monitor` into the `custom_components`
directory in your Home Assistant configuration, restart Home Assistant, and add
**Discovery Monitor** under **Settings → Devices & services**. Existing YAML
configuration remains supported.

## Ignoring discoveries

Open the Discovery Monitor options and select **Recent discoveries**. Choose a
finding and, depending on which stable information it provides, one or both of
these actions are offered:

- **Ignore this device in the future**
- **Ignore this device type in the future**

The device choice prefers the discovery `unique_id`. Without one, only a stable
hardware identifier appropriate to the discovery source is accepted. IP
addresses, ports, signal values, flow IDs, and private or randomized Bluetooth
addresses never create a device rule.

A device-type rule uses the integration plus stable model, product,
manufacturer, service, and type identifiers. Device-specific values such as
addresses, serial numbers, or `unique_id` are excluded. If there is not enough
stable information, that choice is not shown.

Creating a rule immediately aborts the finding's current discovery flow when it
still exists. Matching future flows are logged and aborted before they remain
visible as normal new-device discoveries. No entities are created. Rules can be
removed from the separate **Ignored devices and device types** options page;
removing a rule affects only future discoveries.

Rules and the discovery log use Home Assistant's storage helper. Repeated
identical finds update their first-seen time, last-seen time, and occurrence
count rather than creating duplicates. At most the 100 most recently seen
distinct findings are retained; ignore rules are not affected by this limit.

## Automation example

This generic example writes each discovery to the system log:

```yaml
automation:
  - alias: Log newly discovered integrations
    triggers:
      - trigger: event
        event_type: discovery_monitor_discovered
    actions:
      - action: system_log.write
        data:
          level: info
          message: >-
            Discovered {{ trigger.event.data.domain }} via
            {{ trigger.event.data.source }}:
            {{ trigger.event.data.title or trigger.event.data.address or 'unknown' }}
```

Telegram can be used by a normal Home Assistant automation, but it is not a
dependency of this integration:

```yaml
triggers:
  - trigger: event
    event_type: discovery_monitor_discovered
actions:
  - action: telegram_bot.send_message
    data:
      message: >-
        New {{ trigger.event.data.source }} discovery:
        {{ trigger.event.data.domain }} — {{ trigger.event.data.title }}
```

## Configuration

The UI entry monitors all Home Assistant discovery sources with default
settings. Existing installations can alternatively keep the YAML key:

```yaml
discovery_monitor:
```

Optional filters use the same behavior as the previous options flow:

```yaml
discovery_monitor:
  enabled: true
  include_discovery_data: true
  debug_logging: false
  include_sources: ""
  exclude_sources: ""
  include_domains: ""
  exclude_domains: ""
```

These settings allow you to:

- enable or disable monitoring;
- include or exclude comma-separated discovery sources;
- include or exclude comma-separated integration domains;
- omit `discovery_data` entirely;
- enable non-sensitive debug summaries.

Lists accept comma-separated values. Exclusions take precedence over
inclusions. Configuration changes require a Home Assistant restart.

## Architecture and compatibility

Home Assistant 2026.x's config flow manager calls `async_subscribe_flow()` only
after a non-user-initiated flow has completed its initial step and remains in
progress. That makes it more precise than reacting to the payload-free,
debounced `config_entry_discovered` event or polling `async_progress()`.

Discovery Monitor uses the public `async_subscribe_flow()` and `async_get()` flow
manager methods for lifecycle, identity, source, domain, and unique ID. Home
Assistant does not expose the initial discovery service-info object through
`async_get()`. To provide useful metadata, the small `FlowDataAdapter` reads the
flow manager's private `_progress` mapping and the flow's `init_data` and
`cur_step`. This internal dependency is isolated and defensive: missing or
changed internals produce an event with fewer optional fields and never prevent
Home Assistant from starting.

Only a fixed allowlist of fields is extracted. Unknown nested objects and keys
related to passwords, tokens, credentials, authentication, cookies, private
keys, or secrets are never emitted. Bluetooth addresses are intentionally
allowed.

## Known limitations

- Flows that abort or create an entry during their first discovery step are not
  user-visible and are intentionally not reported, matching Home Assistant's own
  definition of a new discovery notification.
- Titles and unique IDs depend on what the target integration places in its flow
  context. Missing information is not guessed.
- The private enrichment adapter may return no `discovery_data` after a Home
  Assistant internal refactor until compatibility is updated.
- Home Assistant suppresses matching discovery flows before the subscription is
  notified. Discovery Monitor additionally de-duplicates repeated callbacks for
  the same flow ID, but a genuinely new flow can be reported again later.

## Development

```bash
python -m pip install -r requirements_test.txt
ruff check .
pytest
```

GitHub release drafts are created automatically when a maintainer deliberately
pushes a `v*` version tag.

Version `0.2.4` targets Home Assistant 2026.9 or newer. The project is licensed
under the [MIT License](LICENSE).
