# Changelog

All notable user-facing changes to this integration are documented here.

This changelog starts at **v1.3.14** — earlier versions were not tracked.

---

## v1.3.18

### Fixed — a new filtered entity could not be created on recent Home Assistant versions

- **"Error adding entity" on a new filtered entity.** A new filtered entity was first registered under an id taken from its name (`sensor.temperature_filtered`), then renamed to its real id (`sensor.lp_temperature`). On recent Home Assistant versions the rename came while the entity was still being added, and the creation failed; on older ones, the rename removed and re-added the entity, which stopped its silence detection until the next restart. The entity is now registered directly under its real id. Existing entities keep theirs. An entity_id renamed in Home Assistant is kept, instead of being set back, and a broad pattern such as `sensor.*` no longer filters the filtered entities themselves.

### Fixed — `deadband_tau_sigma` had no effect on entities created since March 2026

- **The time constant of the σ estimate stayed at its minimum.** The adaptive deadband estimates σ with an EMA whose time constant starts at 10 × `tau` and grows with time up to `deadband_tau_sigma`. Its starting time was only recorded when the filter had no value yet — but since March 2026 the filter gets its value (last state, or the source's) before any sample. On every entity created since, the time constant stayed at 10 × `tau`, and `deadband_tau_sigma` changed nothing; the "Publish blocked by max_rate_dt" message never appeared. Entities created before kept their starting time across restarts and were not affected. The starting time is now recorded at the first sample, including on entities saved without it.

### Fixed — the silence injection ran outside Home Assistant's event loop

- **Filter state changed from a worker thread.** The timers that detect a silence and inject the last value ran in a worker thread of Home Assistant, which changed the filter state while the event loop used it: the result of a silence could depend on how the two interleaved (seen in the tests: the same silence ended differently from one run to the next). Python 3.14, on which Home Assistant now runs, also reports it as an error in debug mode. They now run in the event loop.

### Fixed — patterns

- **A source without `unique_id` lost its filtered entity at each start.** A sensor that is not in the entity registry (a YAML sensor without `unique_id`) was filtered as soon as it changed, but the startup scan only looked at the registry: its filtered entity was then removed as an orphan at each start — with the name, icon or area set in the UI — and created again at the next change. The startup scan now also looks at the sensors that have a state.
- **Nothing was scanned after a reload.** Patterns were scanned, and orphans cleaned up, only when Home Assistant finished starting: after a reload of the integration, a matching source got its filtered entity back only at its next change. The scan now runs at once when Home Assistant is already running.
- **Dates and text sensors were filtered as numbers.** A pattern also matched sensors whose state is not a measurement, and `2026-10-10` was read as 2026 with `-10-10` as its unit. A state is now a number, optionally followed by a unit that does not start with a digit or a separator: dates, times, version numbers and decimal commas are not measurements. Patterns only create filtered entities for numeric sources.

### Fixed — the name of the filtered entity follows a renamed source

- **A source renamed in Home Assistant kept its old name on the filtered entity until the next restart.** The friendly name was taken from the source once, at its first change. It now follows every rename of the source, and the state is written only when the name changes — never at each value of the source.

### Fixed — decimals

- **The first value had one decimal more than the next ones.** With the dynamic rounding (no `round` set), the number of decimals is derived from the deadband, with a hysteresis so that it does not flicker. The first value used a different formula, which gave one decimal more than every following value with the same deadband. Both now agree.

---

## v1.3.17

### New — angles detected automatically (`circular`)

- **A wind direction filtered without `circular` was averaged as a plain number.** Around the north, 359 and 1 averaged to 180 — the filtered value swung to the south each time the wind crossed 0/360. Angles are now detected automatically: a source with state class `measurement_angle`, or a unit of exactly `°` (not `°C` or `°F`), is filtered as circular with a period of 360, without any configuration. The detection is logged.
- **Values of `circular`** — the same as the `circular` option of [history-explorer-card](https://github.com/Cook23/history-explorer-card) (v1.1.46), which draws angles without jumps at 0/360:
  - absent, `null` or `none`: automatic (new — see above);
  - `false`: never circular (new — it used to be read as a period of 0, with a warning);
  - a number, quoted or not (`360`, `"360"`, `6.28`): that period (unchanged);
  - `2pi`: a period of 2π — upper or lower case, spaces now ignored (`2 PI`);
  - anything else, or a period of 0 or less: circular mode off, with a warning in the log (unchanged).
- **Switching to circular keeps a sane deadband.** A filter state saved in the other mode — a wind direction averaged as a plain number before this version, whose σ reaches ~150° — would have held the adaptive deadband wide open for hours. When the period changes (circular detected after the upgrade, or turned on or off in the configuration), the filter restarts from the source's current value with fresh deadband statistics. The period is now saved with the filter state.
- A circular value is always published within [0, period): 359.9996 rounded to 2 decimals is published as 0, not 360.

---

## v1.3.16

### Fixed — better entity management

- **Filtered entity never created when its source was unavailable at startup.** A pattern-matched source that was `unavailable` when Home Assistant finished starting was skipped by the startup scan, and its later creation was then blocked because the filtered entity already existed in the entity registry from a previous session. The filtered entity stayed an inactive Home Assistant placeholder until the next restart, with no unit of measurement — so any value written to it (by an automation, for instance) reached the Recorder without a unit. The filtered entity is now created as soon as its source reports a valid value.
- **Orphaned filtered entities left behind when no pattern is configured.** Filtered entities whose explicit entry has been removed from the configuration are cleaned up at startup, but that cleanup only ran when at least one pattern was configured. With explicit entries only, a removed entry left its filtered entity behind as an unavailable leftover to delete by hand. The cleanup now always runs.

### Fixed — filtering

- **End-of-silence marker skipped after a real silence (regression in v1.3.15).** The marker decision reused the regular publish test, which compares the filter output with the last published value. Since the v1.3.15 zero-order hold, the output only starts moving toward a newly arrived value at the next sample, so at the moment the source resumed the output was still on the plateau and the marker was skipped — Home Assistant drew a diagonal across the whole silence again. The marker is now decided on the newly arrived value itself: it is published when the output converged during the silence and the new value departs from the published plateau by at least the deadband.
- **Jump of the filtered value after a Home Assistant restart.** At startup, the source's current value was used for the first injection with the whole downtime as its time step, as if that value had been in effect during the entire shutdown. With a downtime comparable to `tau`, the output could jump a large fraction of the way toward that startup value (for example from 400 to about 200 with `tau: 60`, a one-minute restart and a source reading 0 at startup), then come back. The startup value is now handled as a regular sample under the same zero-order hold rule: the downtime is weighted on the last value known before shutdown, and the startup value only takes effect from startup onward.
- The filter state restored at startup is no longer overwritten by the last published (rounded) value, which could introduce a small step.
- Non-finite source values (`nan`, `inf`) are now ignored instead of permanently corrupting the filter state.

### Fixed — configuration and logging

- **Log flooded with `Publish blocked by max_rate_dt` warnings with a fixed deadband.** The warning is meant to flag an adaptive deadband that keeps hitting the rate limiter once σ has settled. With a fixed deadband — `deadband: 0` in particular, which every sample exceeds — reaching `max_rate_dt` is the normal behavior, and a 1 Hz source logged a warning for nearly every blocked sample (several hundred in 20 minutes). The warning is now only issued in adaptive mode.
- **Inconsistent settings for a source matching several patterns.** At startup the last matching pattern applied, while a source appearing later took the first one, so the same source could be filtered with a different `tau` depending on when its filtered entity was created. The first matching pattern in the list now applies in both cases.
- **Unknown configuration keys silently ignored.** A typo — `sensor:` instead of `sensors:`, or a misspelled parameter — disabled the affected entries or parameters without any message. Unknown keys are now reported as warnings in the log.

### Documentation

- The README now describes the adaptive deadband accurately: σ measures the variability of the filtered output — noise, plus any real movement within the `deadband_tau_sigma` window — not noise alone.

---

## v1.3.15

### Fixed

- **End-of-silence marker firing without a real silence.** The marker introduced in v1.3.14 was gated only on the silence timer having expired, not on whether the output had actually had time to converge to the frozen source value. With a short `tau` relative to the source's real update rate, the timer could expire and the source resume again before a single injection had meaningfully moved the filtered output — the marker still fired, publishing the raw source value even though the output was nowhere near it. This showed up as a brief, incorrect spike/dip in the filtered curve (reported as [#3](https://github.com/Cook23/lowpass_dt/issues/3) by [@capeleiro](https://github.com/capeleiro)). The marker now also requires the output to have converged to the last known source value (using the same convergence check already used for injected updates) before firing — if the source resumes before convergence, there was never a real flat plateau to mark, so none is published.

### Changed

- **Zero-order hold (ZOH) time-aware integration.** The low-pass filter now applies `dt[n]` to the *previous* known value (`x[n-1]`) instead of the newly arrived one (`x[n]`):

  ```
  y[n] = y[n-1] + alpha[n] * (x[n-1] - y[n-1])
  ```

  Previously, `dt[n]` was applied to `x[n]`, which implicitly assumed the new value had been in effect for the entire preceding interval. For sparse or impulsive signals (short spikes on an otherwise infrequently-updating source), this overweighted the spike relative to its actual duration. The ZOH formulation fixes this by only ever weighting a value by the time it was actually known to be in effect.

  This introduces a one-sample delay and is the new default behavior — no configuration option is provided to revert to the previous formulation. The effect is negligible on continuous, regularly-sampled signals and only becomes noticeable on sparse, spiky sources — exactly the case this change addresses.

  Suggested by [@capeleiro](https://github.com/capeleiro) in [#4](https://github.com/Cook23/lowpass_dt/issues/4).

---

## v1.3.14

### Fixed

- **End-of-silence marker to avoid misleading diagonal interpolation.** When a source resumes after a silence period, Home Assistant's `line` graph mode only has two recorded points to work with — the last value before silence and the first value after resume — and draws a straight diagonal between them. This visually suggests the value was progressively changing throughout the silence, when in reality the source simply stopped reporting and the true value stayed flat the whole time. The graph should show a horizontal plateau, not a slope.

  A marker point is now published just before the source resumes, carrying a value almost identical to the one recorded right before silence — deliberately *not* strictly identical, since the Recorder would otherwise consider it insignificant and discard it. This forces Home Assistant to render a flat plateau followed by a sharp step, correctly reflecting that the value was frozen during the silence rather than drifting.

### Changed

- `should_publish()` now compares against the sensor's actual published state (`sensor._attr_native_value`) instead of a separate internal copy (`core.last_published`), removing a possible desync between what the filter believes was last published and what Home Assistant actually shows. `core.last_published`, along with the now-unused `finalize_publish()` / `export_state()` / `import_state()` helpers built around it, has been removed; `core.time_last_pub` is set directly.
- `should_publish()` accepts a new `marker` parameter, allowing a check to be performed without triggering a real publish — used internally to decide when the end-of-silence marker should fire.
