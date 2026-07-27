[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/v/release/Cook23/lowpass_dt?style=for-the-badge)](https://github.com/Cook23/lowpass_dt/releases)
[![GitHub stars](https://img.shields.io/github/stars/Cook23/lowpass_dt?style=for-the-badge)](https://github.com/Cook23/lowpass_dt/stargazers)
![Experimental](https://img.shields.io/badge/status-experimental-yellow?style=for-the-badge)
![Math Driven](https://img.shields.io/badge/design-math%20driven-black?style=for-the-badge)

<a href="https://buymeacoffee.com/thierry_couquillou" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" height="50"></a>

# Lowpass DT – Deterministic Time-Aware Filter for Home Assistant

> ⚠️ **Experimental – tested by exactly one person: me.**
> Works well in my environment. May break yours. Back up Home Assistant before installing.
> Extensive logging is intentional during this validation phase and will be reduced in future stable versions.

---

## 📋 Version highlights

A quick look at the milestones — see [CHANGELOG.md](https://github.com/Cook23/lowpass_dt/blob/main/CHANGELOG.md) for the complete, version-by-version detail.

- **v1.3.16** — Fixed a crash that could freeze the filtered output after a Home Assistant restart, introduced in v1.3.15.
- **v1.3.15** — Zero-order hold (ZOH) time-aware integration: `dt[n]` is now applied to the previous known value instead of the newly arrived one, fixing incorrect time weighting on sparse/impulsive signals.
- **v1.3.14** — End-of-silence marker to avoid misleading diagonal interpolation on `line` graphs after a silence period.

---

## 📑 Table of contents

- [Version highlights](#version-highlights)
- [Objective](#objective)
- [Why This Exists](#why-this-exists)
- [What Makes It Different](#what-makes-it-different)
- [Install](#install)
- [Configuration](#configuration)
- [Fine-Tuning](#fine-tuning)
- [Parameters](#parameters)
- [Architecture](#architecture)
- [Performance](#performance)
- [Known Limitations](#known-limitations)
- [License](#license)
- [Author](#author)
- [References](#references)

---

## 🎯 Objective

This integration exists to:

**Keep only what is significant in your measurements and discard the rest.**

It is designed to:

- Prevent unnecessary state updates
- Avoid flooding the Recorder database
- Handle sensors that:
  - Publish whenever they want
  - Stop publishing without warning
  - Resume at random intervals
- Work in batch mode without per-sensor tuning
- Automatically compute statistical parameters
- Preserve signal integrity
- Avoid false frozen values during silence

---

## ❗ Why This Exists

Home Assistant already provides filters.

However:

- Standard filters are not Δt-aware.
- Most filters assume regular sampling.
- During long silences, many filters simply freeze the last value.
- Frozen values are mathematically incorrect.
- Frozen values pollute the Recorder with incorrect states.
- No built-in filter properly handles irregular sampling + silence + adaptive deadband.

Home Assistant's own built-in low-pass filter, for instance, has nothing left to react to once its source goes silent — no new events means no further computation, so its output simply stays wherever it was when the source stopped, however far that is from reality (a heater that was ramping up when its power sensor died, for example, stays frozen mid-ramp).

This component does.

---

## 🧠 What Makes It Different

### ✔ Time-aware integration (Δt-based, zero-order hold)

Handles irregular update intervals correctly:

```
dt[n]    = t[n] - t[n-1]
alpha[n] = dt[n] / (tau + dt[n])
y[n]     = y[n-1] + alpha[n] * (x[n-1] - y[n-1])
```

The filter time constant `tau` is always expressed in real seconds, regardless of how often the source updates. A sensor publishing every 5 seconds and one publishing every 5 minutes will both be filtered with the same physical time constant if `tau` is identical.

The integration uses a **zero-order hold (ZOH)** formulation: `dt[n]` — the time that has just elapsed — is applied to `x[n-1]`, the value that was actually in effect during that interval, not to the newly arrived `x[n]`. The new sample only becomes "the value in effect" for the *next* interval. This matters for sparse or impulsive signals: a short-lived spike arriving after a long silence is not mistakenly weighted as if it had lasted the whole preceding interval. The trade-off is a one-sample delay, which is negligible for continuous, regularly-sampled signals and only becomes visible on sparse, spiky sources — exactly the case it was designed to fix.

- No sample-rate dependency.
- No overshoot. No instability.
- Acts as a true first-order low-pass filter.
- Correct behavior after long gaps — no artificial jump on resume.
- Correct time attribution for irregularly sampled, impulsive signals.

---

### ✔ Silence detection

When a sensor stops publishing for longer than:

```
dt_silence = mean(dt) + 3σ
```

The silence threshold is learned automatically from the source's observed update rate — this is a *timing* estimate (how long between updates), completely independent from the adaptive deadband (which estimates the *value*'s noise level). Disabling the deadband with `deadband: 0` has no effect on silence detection, and vice versa. When silence is detected:

- Synthetic updates are injected at the natural source rate.
- The filter converges smoothly toward the last known real value.
- The final published output equals the last real value received before silence.
- When the source resumes, an end-of-silence marker is published to ensure correct graph representation.

No frozen fake values. No interpolation artifacts.

Unlike Home Assistant's built-in low-pass filter, which simply has nothing left to do once its source stops emitting events, lowpass_dt keeps working during silence: it actively drives the output toward the source's last known value instead of leaving it wherever it happened to be.

Like the adaptive deadband, this threshold is learned from an EMA and assumes there's a real gap between "normal update rhythm" and "actual silence" to detect. If a source already publishes near Home Assistant's practical ceiling (~1 reading/second) with a short `tau`, that learned threshold can end up quite tight, and the mechanism may occasionally trigger on ordinary timing jitter rather than a real silence. This is a much narrower edge case than the deadband one above — a false trigger here only starts an unnecessary injection cycle and does not, by itself, publish an incorrect value — but it's worth knowing about if you're pushing `tau` close to your source's real update interval.

---

### ✔ Adaptive deadband

Optional adaptive deadband:

```
deadband = k × sigma(filtered_signal)
```

The deadband threshold is estimated from the signal's own variability over time. It suppresses noise-induced recorder writes without requiring any manual threshold configuration.

- Self-tuning — adapts to signal noise automatically.
- Eliminates micro-noise while preserving meaningful variations.
- Falls back to a fixed deadband if `deadband` is explicitly set.
- Integral correction ensures slow drifts are still captured even below the threshold.

---

### ✔ Recorder-friendly

- Suppresses insignificant updates — recorder writes divided by ~10 on typical sensors.
- Keeps long-term statistics meaningful.
- Designed for high-frequency sensors (power, temperature, weather...).
- Pattern mode allows a single config line to cover dozens of sensors.

---

## 📦 Install

### HACS (recommended)

Add this repository as a **custom repository** in HACS: `https://github.com/Cook23/lowpass_dt`

1. HACS → ⋮ (top right) → Custom repositories
2. Repository: `https://github.com/Cook23/lowpass_dt`
3. Category: **Integration**
4. Install
5. Restart Home Assistant

### Manual install

1. Download this repository
2. Copy the `lowpass_dt` folder into `config/custom_components/`
3. Restart Home Assistant

---

## ⚙ Configuration

### Explicit Configuration Example

```yaml
lowpass_dt:
  sensors:
    - source: sensor.temperature_raw
      tau: 120
      prefix: lp_
      suffix: "(Filtered)"
      deadband_k_sigma: 2.0
      min_rate_dt: 3600
      max_rate_dt: 10
```

Except for `source`, all parameters are optional. Default values are generally sufficient.

---

### Batch Configuration Example

```yaml
lowpass_dt:
  patterns:
    - match: "sensor.temperature_*"
```

No per-sensor tuning is required because parameters adapt automatically.

---

## 🔧 Fine-Tuning

- Disable the deadband by setting `deadband: 0`
- Plot historical curves of the source and filtered measurements
- Adjust `tau` to filter out unwanted noise while preserving meaningful variations
- Then choose:
  - a fixed deadband value, or
  - return to automatic deadband mode

If you choose automatic deadband:

- Remove the `deadband` parameter
- Wait approximately `300 × tau` for stabilization
  - if `tau = 1 minute`, wait at least 5 hours
  - if `tau = 1 hour`, wait at least 15 days
- Adjust `deadband_k_sigma` if needed:
  - Increase to make the filter less sensitive
  - Decrease to make the filter more sensitive

---

### ❗ Deadband Formula

The implementation uses an integral deadband formula:

```
e = y - y_last_published
i = (e * dt) / tau
Publish if |e| >= D OR |i| >= D
```

This means that a small variation, smaller than the deadband threshold, will still be recorded if it persists long enough. The time constant of this integral action is the same as the main low-pass filter `tau`.

---

## 📘 Parameters

| Parameter | Type | Unit | Default | Description |
|-----------|------|------|---------|-------------|
| `source` | string | — | required | Source sensor entity_id |
| `match` | string | — | required | Source sensor match string (pattern mode) |
| `tau` | float | seconds | 60.0 | Low-pass time constant |
| `prefix` | string | — | `lp_` | Prefix for generated entity_id |
| `suffix` | string | — | `(Filtered)` | Suffix added to the friendly name |
| `name` | string | — | None | Explicit friendly name (disables prefix/suffix) |
| `unique_id` | string | — | auto | Optional unique_id seed (explicit sensors only) |
| `deadband` | float | same unit as the source sensor | None | Fixed deadband threshold, in the source's own unit (W, °C, %, …). If set, disables the automatic (adaptive) deadband entirely — see below. |
| `deadband_tau_sigma` | float | seconds | max(100×tau, 10) | Time constant of the exponential moving average used to estimate the filtered signal's own noise level (σ). Larger values make σ — and therefore the adaptive deadband — react more slowly to changes in noise level; smaller values track recent noise more tightly. Only relevant when `deadband` is not set. |
| `deadband_k_sigma` | float | dimensionless (multiplier) | 2.0 | Multiplies the estimated noise σ to get the adaptive deadband threshold (`deadband = k × σ`). Higher = less sensitive (fewer updates, more noise tolerated); lower = more sensitive. Only relevant when `deadband` is not set. |
| `min_rate_dt` | float | seconds | 3600 | Maximum interval between publishes (heartbeat) |
| `max_rate_dt` | float | seconds | 10 | Minimum interval between publishes (rate limiter) |
| `round` | int | number of decimal places | auto | Number of decimal digits to round the published value to (e.g. `round: 2` → `12.345` becomes `12.35`). If omitted, decimals are chosen automatically and adjusted dynamically from the effective deadband. |
| `circular` | string | same unit as the source sensor (a period) | None | Period for circular sensors (`360`, `2pi`, …) |
| `silence` | string | — | None | Value published after convergence: `last` (default), `zero`, `unknown` |
| `debug` | boolean | — | false | Enable verbose attributes |

A match string should avoid matching already filtered entities. A prefix is added to the generated entity_id to prevent this. Recursion is automatically blocked if a misconfigured match string matches filtered entities. Creation is limited to 100 entities per match string.

`min_rate_dt` and `max_rate_dt` name a *duration* (`dt`, an interval in seconds), not a *rate* — the "min"/"max" refers to the resulting publish rate they enforce, not to the duration itself. This is the same inversion as frequency vs. period: a *maximum* frequency corresponds to a *minimum* period, and vice versa. So `min_rate_dt` (the smallest allowed rate) is enforced by the *largest* interval — it's the heartbeat ceiling, firing at most this rarely. `max_rate_dt` (the largest allowed rate) is enforced by the *smallest* interval — it's the rate limiter floor, firing at most this often.

`min_rate_dt` ensures a minimum publish rate even when the signal remains stable while the source is not silent.
`max_rate_dt` is a last line of defense against flooding the Recorder and should almost never be reached.

`silence` controls what value is published after the filter converges during silence. Use `zero` for sensors where silence means the device is off (power, current...) and the source failed to transmit that final zero. Use `unknown` when the value during silence is genuinely indeterminate. For `total` and `total_increasing` sensors this parameter has low effect — the end-of-silence marker is omitted so HA interpolates diagonally, which correctly reflects ongoing accumulation.

### Deadband parameters in practice

There are two mutually exclusive ways to control the deadband:

- **Fixed** — set `deadband` to a number in the source's own unit. For example, `deadband: 5` on a power sensor (W) means the filtered output only republishes once it has moved by at least 5 W from the last published value (see the integral correction rule above for the slow-drift exception). Once `deadband` is set, `deadband_tau_sigma` and `deadband_k_sigma` are ignored.
- **Adaptive (default)** — leave `deadband` unset. The integration estimates the signal's own noise level (σ) over a rolling window of `deadband_tau_sigma` seconds, and sets the effective deadband to `deadband_k_sigma × σ`. This is what "self-tuning" means in practice: a noisier sensor gets a wider deadband automatically, a quiet one gets a narrower one, without you measuring anything by hand.

#### When the adaptive deadband isn't the right tool

The adaptive deadband assumes there's real statistical oversampling to exploit — that within the noise-estimation window, the source is reporting the *same underlying value* multiple times with some scatter around it, so that scatter can be measured and told apart from genuine movement. If the source already publishes at Home Assistant's practical ceiling (effectively ~1 reading/second, with no faster internal sampling behind it) every single reading is itself a real, distinct measurement — there's no oversampling left to average out, and a fast, real transition (e.g. a battery hitting empty and its power reading dropping hard) looks statistically identical to noise. In that regime, σ can spike right when it shouldn't, and the deadband can temporarily balloon well past the point of being useful, delaying updates for a while after a genuine step change.

This isn't a bug to tune away — no purely statistical rule can reliably separate "noise" from "signal" once they share the same characteristic frequency; the two concepts stop being distinguishable at all. If you're filtering a source that's already at (or near) that native update ceiling and you see the output getting stuck for a while after a real step change, set `deadband: 0` to disable the adaptive estimation entirely and rely on `tau` alone for smoothing.

```yaml
# Fixed deadband: republish only on a change of at least 5 W
lowpass_dt:
  sensors:
    - source: sensor.oven_power_raw
      tau: 30
      deadband: 5

# Adaptive deadband, tuned to react faster to changing noise levels
# (useful on a sensor whose noise level itself varies a lot over the day)
lowpass_dt:
  sensors:
    - source: sensor.wind_speed_raw
      tau: 20
      deadband_tau_sigma: 600     # re-estimate noise level over 10 minutes
      deadband_k_sigma: 1.5       # more sensitive than the 2.0 default

# Source already publishing near HA's practical ceiling (~1/s): no
# oversampling to exploit, so the adaptive deadband is disabled outright
lowpass_dt:
  sensors:
    - source: sensor.battery_power_raw
      tau: 5
      deadband: 0

# Fixed rounding to 1 decimal, regardless of the deadband
lowpass_dt:
  sensors:
    - source: sensor.room_temperature_raw
      tau: 300
      round: 1
```

---

## 🏗 Architecture

- **LowpassCore** → Pure math engine (Δt-aware, zero-order hold, adaptive sigma, deadband)
- **TauInjector** → Silence detection & injection
- **Publisher** → Home Assistant state exposure
- **HA-native restore** → Clean persistence

Event-driven, no polling, no background loops.

---

## 📈 Performance

- Event driven, no background loops
- Injection active only during silence
- Fixed cost per update, regardless of sensor count

---

## ⚠ Known Limitations

- No ConfigFlow UI yet
- Not reviewed for HA Core inclusion
- Experimental default tuning
- Both the adaptive deadband and silence detection are learned from EMA statistics, and both assume a real gap between "normal" and "anomalous" on their respective axis (value noise, update timing). If a source already publishes near Home Assistant's practical update ceiling with a short `tau`, that gap can shrink to the point where the assumption breaks down — see [When the adaptive deadband isn't the right tool](#deadband-parameters-in-practice) for the value-side case, and the note under [Silence detection](#-silence-detection) for the timing-side one. There is currently no dedicated option to bypass silence detection the way `deadband: 0` bypasses the adaptive deadband.
- Other edge cases may exist

---

## 📜 License

MIT

---

## 👤 Author

Built to solve a real problem: filtering real-world asynchronous sensors without lying to the math.

If you have experienced incorrect frozen values at the output of a filter, or seen filtered values behave erratically when the sensor reporting rate changes, this integration is for you.

This is my first Home Assistant integration and my first software development project in Python. I come from the industrial automation and process control world, where C is king.

So yes, errors and mistakes are absolutely possible. Please be kind.

---

## References

### Zero-Order Hold and Correct Time Attribution

Applying `dt[n]` to `x[n-1]` instead of `x[n]` is a standard **zero-order hold (ZOH)** reconstruction: between two samples, the only value actually known to have been in effect is the previous one. This avoids attributing the full preceding interval's weight to a value that has only just arrived — a bias that is invisible on smooth, densely-sampled signals but significant on sparse or impulsive ones (short spikes, event-driven sensors, long gaps followed by a brief excursion).

This is closely related to:

- **Zero-Order Hold (ZOH) reconstruction**, as used in sampled-data and digital control systems
- **Causal filtering** — the filter output at time `t[n]` never depends on information only available after `t[n-1]`

### Adaptive Delta Encoding for Gaussian Noise

This filter implements a form of **adaptive delta encoding** (also known as *send-on-delta* or *level-crossing sampling*) optimized for Gaussian noise environments.

Instead of transmitting every sampled value, the system:

- Applies a first-order low-pass filter
- Dynamically estimates the noise level (σ)
- Publishes only when the filtered signal deviates from the last published value by more than `k·σ`

When `k = 2`, the probability that pure Gaussian noise triggers a transmission is approximately **5%**, making the encoder statistically near-optimal for suppressing noise-induced events while preserving meaningful signal variations.

This approach is closely related to:

- **Adaptive Delta Modulation (ADM)**
- **Level-Crossing Sampling**
- **Send-on-Delta transmission schemes**
- Statistical thresholding based on **Rice's level-crossing theory**

#### Bibliography

- Rice, S. O. (1944–1945). *Mathematical Analysis of Random Noise*. Bell System Technical Journal.
- Proakis, J. G., & Salehi, M. *Digital Communications*. McGraw-Hill.
- Gubner, J. A. *Probability and Random Processes for Electrical and Computer Engineers*. Cambridge University Press.
- Åström, K. J., & Wittenmark, B. *Computer-Controlled Systems: Theory and Design*. Prentice Hall. (Zero-order hold reconstruction)
