# lowpass_dt — notes for Claude sessions

Home Assistant custom integration (HACS): a time-aware low-pass filter for sensors.
Code in `custom_components/lowpass_dt/`; user-facing changes go in `CHANGELOG.md` and the
README "Version highlights".

## Shared logic with history-explorer-card

[Cook23/history-explorer-card](https://github.com/Cook23/history-explorer-card) (a Lovelace
card, JavaScript) is a different project by the same author, but it reuses two pieces of
logic from this integration and documents them as "the same as lowpass_dt". A change to
either piece here must be mirrored there (and vice versa), or the docs on both sides become
wrong. The card is worked on in its own Claude session; tell the user when a change here
needs a matching change in the card.

### 1. `circular` option values — must stay identical

| here | card |
|---|---|
| `config.py` (parsing), `sensor.py` `_resolve_auto_circular` (auto-detection) | `src/history-explorer-card.js` `_circularPeriod` |

- absent / `null` / `none` (trimmed, any case): automatic — state class `measurement_angle`,
  or a unit of exactly `°` (not `°C`/`°F`), gives a period of 360;
- `false`: never circular;
- a number or numeric string: that period; `2pi` (any case, all spaces removed): 2π;
- anything else (including `true`), or a period ≤ 0 or non-finite: off, with a warning.

Introduced in lowpass_dt v1.3.17 / card v1.1.46. Both READMEs and CHANGELOGs say "same values as" the other.

### 2. Silence detection — same rule, with known, deliberate differences

| here | card |
|---|---|
| `injector.py` `_update_dt_stats`, `_compute_limits`, `set_last_source_time` | `src/history-explorer-card.js` `_applySilencePlateaus` (`lineMode: smart`) |

Common rule: the source's usual interval is an EMA (alpha 0.1) of the intervals between
values, with an EMA of their square for σ. An interval longer than `mean + 3σ + 0.1 s`
(never under 1 s) is a silence, and the first sample after a silence counts as that limit
in the EMA, not as its real length.

Differences (keep them in mind before calling the two "identical"):
- **Seed.** Here the EMA starts from the first interval, with σ = 0, because the filter runs
  online and the state is restored across restarts. The card has the whole history, so it
  starts from the median of the intervals, with σ from their median absolute deviation
  × 1.4826. The card's README says "started from their median".
- **Cap.** Here the silence limit is also capped at `tau` (`max(min(raw, tau), 1)`), because
  it drives the injection of synthetic samples. The card has no `tau` and does not cap it.
- The end-of-silence marker (v1.3.14) and the `silence` option (`none`/`last`/`zero`/`unknown`)
  exist only here.
