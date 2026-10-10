# Branch: v1.4-dev

This branch contains the development version 1.4 (beta).

DO NOT assume that code or architecture from main still applies.
This branch is allowed to contain breaking architectural changes.

## The constraint of 1.4: clean code

1.4 exists to make the integration clean and reliable. Every change, features and fixes
alike, must keep it so:
- the entity's lifecycle owned by Home Assistant: entity_id set before the entity is added,
  no forced rename, every part able to stop and start again;
- everything in the event loop (`@callback`), no worker thread;
- each module with its own state behind a small interface: no reaching into another
  module's private attributes, the configuration read-only once parsed;
- dead code removed, comments saying why rather than history, tests for every behavior.
A change that would break this is redesigned, not merged.

## Planned

From the October 2026 review (details in the review's tests, `tests/`):
- the entity_id set at creation, `_deferred_rename` removed, the injector restartable
  (a new entity loses silence detection; a broad pattern filters its own entities);
- the injector's timers in the event loop (`@callback`);
- `alpha = 1 − exp(−dt/tau)`: the filter no longer depends on the sampling rate
  (behavior change, to announce);
- patterns: sources without `unique_id` kept across restarts, scan and cleanup after a
  reload, only numeric sources;
- `Publisher.publish` split; a real integral term, or none;
- configuration validated by a voluptuous `CONFIG_SCHEMA`;
- CI: hassfest, HACS validation, pytest.
