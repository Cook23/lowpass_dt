# Architecture (v1.4)

How the integration's code is organized, what each part is responsible for, and the rules
that keep them apart. What the filter is for, and why it behaves as it does, is in the
[README](README.md); the logic shared with
[history-explorer-card](https://github.com/Cook23/history-explorer-card) is listed in
[CLAUDE.md](CLAUDE.md).

## The parts

```
 configuration.yaml ── __init__.py ── one config entry (the YAML, as is)
                                           │
                                           ▼
                      loader.py ── sensors: / patterns: → one entity per source
                          │          (startup scan, sources appearing later,
                          │           cleanup, recursion guard)
                          ▼
 source entity ──► sensor.py LowpassDtSensor ──► Home Assistant state (and Recorder)
  state_changed      │  value read: config.parse_state, or attributes.py
                     │  restore / save across restarts
                     ├──► filter.py   LowpassCore  ── the first order, σ, the deadband
                     ├──► injector.py TauInjector  ── timers: silence, injection, tracking
                     └──► publisher.py Publisher   ── whether, what and when to write
```

| File | Role |
|---|---|
| `__init__.py` | The YAML section kept as the data of a single config entry: created, updated (and reloaded) or removed with it. The schema is deliberately permissive: each option is checked further down, a typo warns instead of disabling every filter. |
| `config_flow.py` | The import step creating that entry; there is no UI configuration. |
| `const.py` | The configuration keys — the single list of what the YAML accepts (`loader.py` warns on any other key). |
| `config.py` | An item of the YAML turned into a `LowpassCfg` (validated, defaults, warnings), its name, entity_id and unique_id (`make_meta`), and the parsing of a source state (`parse_state`: a number, optionally followed by its unit). |
| `loader.py` | Which entities exist: the explicit `sensors:` at once, the `patterns:` once Home Assistant has started and for every source appearing later; the cleanup of entities whose source is gone; the guard that never filters a filtered entity. |
| `sensor.py` | **The entity** (`LowpassDtSensor`): the source's events, its unit, device and state class followed, the automatic detection of angles (`circular`), the restore of the filter state across restarts, and the wiring of the three parts below. |
| `attributes.py` | An attribute as the source: its value and unit read as history-explorer-card reads them, its device and state class derived. |
| `filter.py` | **The math** (`LowpassCore`), no Home Assistant: the exact first order with zero-order hold, the circular mean, the σ of the output and the deadband derived from it, with its integral term. |
| `injector.py` | **The time** (`TauInjector`): the source's usual interval and the silence limit, the injection of the last value during a silence, the tracking of the output between two samples. Every timer runs in Home Assistant's event loop. |
| `publisher.py` | **The writes** (`Publisher`): whether the output is written (deadband, `max_rate_dt`, `min_rate_dt`), its rounding, the end-of-silence marker, the value published once converged (`silence`), the debug attributes. |

## The rules

1. **The math knows no time source and no Home Assistant.** `filter.py` is given a value
   and a time; it can be tested alone (`tests/test_core.py`). Timers belong to
   `injector.py`, writes to `publisher.py`.
2. **One computation of the output, whatever calls it.** A real sample, an injected one and
   the tracking between samples all go through `LowpassCore`, and the exact first order
   gives the same result however often it is computed.
3. **An entity's identity never comes from its name.** Its entity_id is set before it is
   added, its unique_id comes from the configuration (`make_meta`); Home Assistant would
   otherwise derive the entity_id from the friendly name, and a broad pattern would filter
   its own entities, endlessly (see `tests/test_entity_id.py`).
4. **A bad option is a warning, never a failure.** It falls back to its default; the other
   filters keep working. It is reported once, when the integration loads: a pattern is built
   again for each of its sources (`build_cfg(..., quiet=True)`), never with its warnings.
5. **Logic shared with history-explorer-card stays the same on both sides** (`circular`
   values, silence detection, attribute units — [CLAUDE.md](CLAUDE.md)): a change here is
   passed on to the card.

## Where a change goes

| To change... | Look in |
|---|---|
| A YAML option | `const.py` (key), `config.py` (validation, default), `README.md` (Parameters) |
| How the filtered value is computed | `filter.py` |
| What a silence is, the injection, the tracking | `injector.py` |
| When and what is written | `publisher.py` |
| How a source's value, unit or classes are read | `sensor.py`, `config.py` (`parse_state`), `attributes.py` |
| Names, entity_id, unique_id | `config.py` (`compute_name_and_slug`, `make_meta`), `sensor.py` (constructor) |
| Which entities are created or removed | `loader.py` |
| What survives a restart | `sensor.py` (`extra_restore_state_data`, `_restore_internal_state`) |

## Tests

`pytest` against a real Home Assistant core ([`tests/README.md`](tests/README.md)): the
options, the math, the parsing, the entity in Home Assistant, the patterns, the entity_id,
attributes as sources, and the filter as a whole (`test_behavior.py`) — in the normal
regime and when the source slows down, compared with the exact first order, and redrawn by
straight lines and splines as third-party tools would. The CI runs them on five Home
Assistant versions, from 2025.1 to 2026.10.
