# PR #3: targeted review

Reviewed on 2026-11-22 on `feature/bluetooth-discovery-management`.

## Confirmed defects and corrections

- **Cached name accepted as model evidence:** with real HA 2026.11.2
  `BluetoothServiceInfoBleak.from_scan` objects, a missing advertisement local
  name can make `info.name` fall back to `BLEDevice.name`. A cached model-shaped
  name plus a matching packet previously offered a Govee/ThermoPro model action.
  The new regression failed before the correction. Recognition now uses the
  public advertisement's `local_name` when available and rejects missing names.
- **Ambiguous diagnostics:** empty manufacturer dictionaries, truncated packets
  and unknown models previously shared `unsupported_or_incomplete_model`.
  Distinct diagnostic reasons now distinguish absent data/name, incomplete
  packets, invalid data, unsupported formats and unsupported models. Existing
  model IDs, rule versions and legacy comparison criteria are unchanged.

These are the only production changes in this review. No speculative storage,
notification, matching or event-payload changes were made.

## Risks not reproduced in regression tests

- **Implicit suppression by stored findings:** after deleting a model rule,
  new flow IDs for the same previously recorded addresses emit events again.
  This passes with and without reloading findings; the native HA test also
  rediscovers an address previously recorded while the model rule was active.
  Independently configured notification pauses remain applicable.
- **Burst notifications:** 100 consecutive callbacks for one identity produce
  one event while all 100 occurrences update the counter. Reset permits the
  next event; its immediate repeat is paused again.
- **Old timestamps returning after reset:** eight controlled interleavings cover
  pending versus in-flight writes, one versus two overlapping resets, and a new
  event during reset versus no new event. HA's real Store lock and disk writes
  are retained, with a barrier after capturing write data. Queued/redundant
  callbacks cannot restore old timestamps. The on-disk JSON and a fresh HA
  instance after normal shutdown contain exactly the expected new state.
  These cases run on both HA 2026.10.4 and 2026.11.2.
- **Legacy rule migration or broadening:** literal pre-PR `device` and
  `device_type` records load without writes or changed serialized state. Rule
  references and full-dictionary equality remain unchanged; extra/missing
  criteria and other domains do not match. `identity.py` is unchanged from main.

## Validation and remaining limits

- Local: **143 pytest cases**, **8 Chromium browser cases**, Ruff/format checks.
- Native HA 2026.11.2: **5 unittest methods**, including actual
  `BluetoothServiceInfoBleak` objects for all three integrations, connectable and
  non-connectable variants, missing/unsupported data and the eight reset races.
- The workflow repeats the native tests using Bluetooth import dependencies
  from HA 2026.11.2's manifests. Required check results are available on
  [PR #3](https://github.com/placix/home-assistant-discovery-monitor/pull/3/checks).
- Advertisement bytes are controlled fixtures, not recordings from the user's
  radios. No real scanner, physical-device identity proof or complete HA 2026.11
  UI test is claimed. The discovery handler is substituted; HA's object types,
  flow manager, callbacks, event bus and Store are real.
- The full pytest fixture package still pins HA 2026.10.4. The separate native
  2026.11 test complements it. Unclean power loss before a delayed save may still
  allow an extra notification; these tests cover orderly shutdown/restart.

README content and version 0.2.10 are preserved. No merge, tag or release.
