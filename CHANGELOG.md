# Changelog

All notable user-facing changes to this integration are documented here.

This changelog starts at **v1.3.14** — earlier versions were not tracked.

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
