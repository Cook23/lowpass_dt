[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/v/release/Cook23/lowpass_dt?style=for-the-badge)](https://github.com/Cook23/lowpass_dt/releases)
[![GitHub stars](https://img.shields.io/github/stars/Cook23/lowpass_dt?style=for-the-badge)](https://github.com/Cook23/lowpass_dt/stargazers)
![Experimental](https://img.shields.io/badge/status-experimental-yellow?style=for-the-badge)
![Math Driven](https://img.shields.io/badge/design-math%20driven-black?style=for-the-badge)

<a href="https://buymeacoffee.com/thierry_couquillou" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" height="50"></a>

# Lowpass DT – Deterministic Time-Aware Filter for Home Assistant

> ⚠️ **Experimental – used in real life by one person: me**, and, since 1.3.18, checked by an automated test suite against a real Home Assistant.
> Works well in my environment. May break yours. Back up Home Assistant before installing.
> Extensive logging is intentional during this validation phase and will be reduced in future stable versions.

> [!IMPORTANT]
> **Version 1.4 is available as a beta**
>
> The **1.4** line is published as a beta (pre-release) alongside this **1.3** line, which remains the recommended version. It follows a full review of the code, and makes the integration cleaner and more reliable; its bug fixes are brought to 1.3 once tested (1.3.18). What 1.4 adds today:
> - **the exact first-order low-pass**, whatever the source's rhythm, and **no more delay of one sample** on slow sources (weather): a new value moves the output at once;
> - **an attribute as the source** (a weather's wind speed, a thermostat's temperature), and `unit`, `device_class` and `state_class` to set them on any filtered entity;
> - **fewer warnings** in the log: normal events are logged at a lower level, warnings are kept for configuration errors.
>
> **To try it**, enable *Show beta versions* for this repository in HACS and download the latest 1.4; to go back, download the latest 1.3 the same way. Feedback is welcome in an [issue](https://github.com/Cook23/lowpass_dt/issues).
>
> *Developed with Claude Code, which modifies, reviews and tests the integration, and runs its automated tests against a real Home Assistant core.*

---

## 📋 Version highlights

A quick look at the milestones — see [CHANGELOG.md](https://github.com/Cook23/lowpass_dt/blob/main/CHANGELOG.md) for the complete, version-by-version detail.

- **v1.3.18** — Bug fixes from the 1.4 review: new filtered entities created reliably on recent Home Assistant versions, silence injection in the event loop, `deadband_tau_sigma` effective again, safer patterns; tested on Home Assistant 2025.1 to 2026.10.
- **v1.3.17** — Angles detected automatically: a source in `°` or with state class `measurement_angle` is filtered as circular (no more 180° averages around the north); `circular: false` turns it off.
- **v1.3.15** — Zero-order hold (ZOH): each value weighted by the time it was actually in effect, for sparse or impulsive sources.
- **v1.3.14** — End-of-silence marker, so that a graph drawn with straight lines shows a flat plateau over a silence instead of a diagonal.

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
dt_silence = min(mean(dt) + 3σ + 0.1 s, tau)      (never less than 1 s)
```

The silence threshold is learned automatically from the source's observed update rate, and capped at `tau`: once `tau` has elapsed with no new value, the last value is treated as still in effect and the filter starts converging toward it. This is a *timing* estimate (how long between updates), completely independent from the adaptive deadband (which estimates the variability of the *value*). Disabling the deadband with `deadband: 0` has no effect on silence detection, and vice versa. When silence is detected:

- Synthetic updates are injected at the source's mean update rate (capped at `tau`).
- The filter converges smoothly toward the last known real value.
- The final published output equals the last real value received before silence.
- When the source resumes, an end-of-silence marker is published to ensure correct graph representation.

No frozen fake values. No interpolation artifacts.

Unlike Home Assistant's built-in low-pass filter, which simply has nothing left to do once its source stops emitting events, lowpass_dt keeps working during silence: it actively drives the output toward the source's last known value instead of leaving it wherever it happened to be.

Like the adaptive deadband, this threshold is learned from an EMA and assumes there's a real gap between "normal update rhythm" and "actual silence" to detect. If a source already publishes near Home Assistant's practical ceiling (~1 reading/second) with a short `tau`, that learned threshold can end up quite tight, and the mechanism may occasionally trigger on ordinary timing jitter rather than a real silence. This is a narrow edge case — a false trigger here only starts an unnecessary injection cycle and does not, by itself, publish an incorrect value — but it's worth knowing about if you're pushing `tau` close to your source's real update interval.

---

### ✔ Adaptive deadband

Optional adaptive deadband:

```
deadband = k × sigma(filtered_signal)
```

The deadband threshold is estimated from the signal's own variability over time, without any manual configuration. σ is measured on the filtered output over `deadband_tau_sigma` (100 × `tau` by default), a period that covers the system's significant events: what the system does all the time is written now and then, what changes its state is written at once.

- Self-tuning — adapts to the amplitude of the system's own variations automatically.
- Divides the writes by 10 or more, while keeping the changes of state.
- Falls back to a fixed deadband if `deadband` is explicitly set.
- Integral correction ensures slow drifts are still captured even below the threshold.

---

### ✔ Recorder-friendly

- Suppresses insignificant updates — recorder writes divided by ~10 on typical sensors.
- Keeps long-term statistics meaningful.
- Designed for chatty sensors (power, temperature…) as well as slow ones (weather).
- Pattern mode allows a single config line to cover dozens of sensors.

---

## 📦 Install

### Home Assistant versions

Since 1.3.18, the integration is tested with automated tests against **Home Assistant 2025.1, 2025.6, 2025.10, 2026.2 and 2026.10**: the creation of filtered entities and their `entity_id` (never derived from the friendly name, no creation loop with a broad pattern), the filter, silences, patterns and restarts. These recent versions are therefore relatively safe; older Home Assistant versions are not covered by the automated tests. Versions of lowpass_dt before 1.3.18 were tested in real use, but not extensively by automated tests.

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

Except for `source`, all parameters are optional. Default values are generally sufficient. Unknown keys — a typo, or `sensor:` instead of `sensors:` — are reported as warnings in the log instead of being silently ignored.

---

### Batch Configuration Example

```yaml
lowpass_dt:
  patterns:
    - match: "sensor.temperature_*"
```

No per-sensor tuning is required because parameters adapt automatically.

---

### Filtering a filtered entity

```yaml
lowpass_dt:
  sensors:
    - source: sensor.power
      tau: 30
    - source: sensor.lp_power       # sensor.lp_lp_power: the filtered power, filtered again
      tau: 600
      name: Power (hourly trend)
```

A filtered entity can be the source of another filter, but only when it is named explicitly in `sensors:`. A pattern never filters an entity of this integration, even when its match string matches it (`sensor.*`): it would most likely be a mistake, and would chain filters endlessly (`lp_lp_lp_…`). The guard does not rely on the prefix: an entity is recognized as filtered by Home Assistant's entity registry, which records the integration that created it — with any `prefix`, an empty one included, and after its `entity_id` was renamed in Home Assistant.

---

## 🔧 Fine-Tuning

Set `tau` first — everything rests on it — then the deadband, then, if needed, the rate limits.

### 1. `tau`: the base of everything

`tau` is the time constant of the low-pass, in seconds. It does not depend on how often the source reports. It is:

- **the time constant of the physical system measured**, or
- **the time below which you consider an event not worth recording** — a kettle running for two minutes hardly matters for the month's bill.

**Estimating the time constant of a system** — an example, very approximate. You come back from holidays. It is night, there is no wind and no sun: nothing unusual disturbs the house, which is at 15 °C. You set the thermostat to 20 °C. The time constant of the house is the time it takes to reach 18 °C — 63 % of the 5 °C step asked for. No need to try it: everyone knows "we'll be cold for a few hours", so `tau` is a few hours. That is enough for a sensible `tau` — and it is the same whether the thermometer reports every second or every minute.

> A house's temperature is closer to an integrator of the heating power than to a first order: seeing it as a first order over a step of a few degrees is a rough approximation, but good enough to choose `tau`.

**The source's period** — the filter works properly when `tau` is at least about **10 times the source's usual reporting period**: it then has enough values to average. A source reporting every minute fits a `tau` of a few hours easily; with a `tau` of 30 s, a source should report every few seconds.

- **To tune it, look at the filtering alone.** Disable the deadband (`deadband: 0`), plot the source and the filtered output together over a day or two, and adjust `tau` until the filtered curve keeps the real variations and drops the noise. Then go on to the deadband.
- **If the source slows down** beyond `tau` for a while (or always reports less often than that), nothing breaks: the output stays within the source's range, without overshoot, and reaches the source's value. It just no longer filters anything useful.
- `tau` also sets the default of `deadband_tau_sigma` (100 × `tau`) and the time constant of the integral correction (below).

### 2. The deadband: what gets written

The deadband is there so as not to flood the Recorder: keep about one point in ten, or fewer, and keep what matters:

- a **change of state of the system** — a big consumer starting — is written at once;
- while the signal just **lives**, a point now and then;
- **no static error**: when the source holds still, the published value ends up equal to it (integral correction, below).

**Automatic (default)** — leave `deadband` unset. The deadband is `deadband_k_sigma × σ`, where σ is the variability of the filtered output over `deadband_tau_sigma` (100 × `tau` by default). That period covers the system's significant events, so the deadband follows the amplitude of what the system usually does.

- **Stabilization.** The time constant of σ starts at 10 × `tau` and grows to `deadband_tau_sigma`; allow about 3 × `deadband_tau_sigma` — 300 × `tau` by default — for the estimate to settle: about 5 hours with `tau` = 1 minute, about 12 days with `tau` = 1 hour.
- **`deadband_k_sigma`** (default 2) mostly sets **how soon** a change is written; the number of writes changes little. Lower: sooner, a few more writes; higher: fewer writes, later.
- **`deadband_tau_sigma`** sets **how long** the deadband remembers what the system does. Longer: it remembers rarer events, and stays wider for longer after them. Shorter: it follows a change of regime sooner, but narrows in quiet periods, so more writes once the signal lives again.

**Fixed** — `deadband` in the source's unit (`deadband: 50` on a power sensor), when you know the size of a significant change, such as the smallest appliance you want to see. `deadband_tau_sigma` and `deadband_k_sigma` are then ignored.

**Disabled** — `deadband: 0`: every filtered value is written, within the rate limits.

### 3. Rate limits

- `max_rate_dt` (10 s): at most one write per this interval — except right after a silence.
- `min_rate_dt` (3600 s): at least one write per this interval, even when nothing changes.

---

### ❗ Deadband Formula

The implementation uses an integral deadband formula:

```
e  = y − y_last_published          (deviation from the last written value)
Δt = time since the last written value
i  = e × Δt / tau
Publish if |e| ≥ D  or  |i| ≥ D
```

A deviation smaller than the deadband `D` is still written if it lasts: the longer it lasts, the larger `i`. Half the deadband is written after 2 × `tau`, a tenth after 10 × `tau`. So the published value ends up equal to the source when it holds still: no static error.

---

## 📘 Parameters

| Parameter | Type | Unit | Default | Description |
|-----------|------|------|---------|-------------|
| `source` | string | — | required | Source sensor entity_id |
| `match` | string | — | required | Source sensor match string (pattern mode) |
| `tau` | float | seconds | 60.0 | Low-pass time constant: the time constant of the system measured, or the time below which an event is not worth recording — ideally at least 10 times the source's usual reporting period (see [Fine-Tuning](#-fine-tuning)) |
| `prefix` | string | — | `lp_` | Prefix for generated entity_id |
| `suffix` | string | — | `(Filtered)` | Suffix added to the friendly name |
| `name` | string | — | None | Explicit friendly name (disables prefix/suffix) |
| `unique_id` | string | — | auto | Optional unique_id seed (explicit sensors only) |
| `deadband` | float | same unit as the source sensor | None | Fixed deadband threshold, in the source's own unit (W, °C, %, …). If set, disables the automatic (adaptive) deadband entirely — see below. |
| `deadband_tau_sigma` | float | seconds | max(100×tau, 10) | Longest time constant of the moving average that estimates the variability (σ) of the filtered output; it starts at 10 × `tau` and grows to this value. Longer: the deadband remembers rarer events; shorter: it follows a change of regime sooner. Only relevant when `deadband` is not set. |
| `deadband_k_sigma` | float | dimensionless (multiplier) | 2.0 | Multiplies the estimated σ to get the adaptive deadband (`deadband = k × σ`). Mostly sets how soon a change is written: lower = sooner, a few more writes; higher = later, fewer writes. Only relevant when `deadband` is not set. |
| `min_rate_dt` | float | seconds | 3600 | Maximum interval between publishes (heartbeat) |
| `max_rate_dt` | float | seconds | 10 | Minimum interval between publishes (rate limiter) |
| `round` | int | number of decimal places | auto | Number of decimal digits to round the published value to (e.g. `round: 2` → `12.345` becomes `12.35`). If omitted, decimals are chosen automatically and adjusted dynamically from the effective deadband. |
| `circular` | string | same unit as the source sensor (a period) | automatic | Period for circular sensors (angles): the filter averages across 0/360 (359 and 1 average to 0, not 180). Absent, `null` or `none`: automatic — a source with state class `measurement_angle` or a unit of exactly `°` (not `°C`/`°F`) is circular with a period of 360. `false`: never. A number or numeric string (`360`, `6.28`): that period; `2pi` (any case, spaces ignored): 2π. Any other value, or a period ≤ 0, disables it with a warning. Never applies to a `total_increasing` source. Same values as the `circular` option of [history-explorer-card](https://github.com/Cook23/history-explorer-card). |
| `silence` | string | — | None | Value published after convergence: `last` (default), `zero`, `unknown` |
| `debug` | boolean | — | false | Enable verbose attributes |

A pattern never filters an entity of this integration, even if the match string matches it (see [Filtering a filtered entity](#filtering-a-filtered-entity)). Creation is limited to 100 entities per match string. When a source matches several patterns, the first matching pattern in the list applies.

`min_rate_dt` and `max_rate_dt` name a *duration* (`dt`, an interval in seconds), not a *rate* — the "min"/"max" refers to the resulting publish rate they enforce, not to the duration itself. This is the same inversion as frequency vs. period: a *maximum* frequency corresponds to a *minimum* period, and vice versa. So `min_rate_dt` (the smallest allowed rate) is enforced by the *largest* interval — it's the heartbeat ceiling, firing at most this rarely. `max_rate_dt` (the largest allowed rate) is enforced by the *smallest* interval — it's the rate limiter floor, firing at most this often.

`min_rate_dt` ensures a minimum publish rate even when the signal remains stable while the source is not silent.
`max_rate_dt` is a last line of defense against flooding the Recorder and should almost never be reached with the adaptive deadband — a warning is logged when it blocks a publish once σ has settled. With a fixed `deadband` (including `0`) it is the normal rate limiter, and blocked publishes are not logged.

`silence` controls what value is published after the filter converges during silence. Use `zero` for sensors where silence means the device is off (power, current...) and the source failed to transmit that final zero. Use `unknown` when the value during silence is genuinely indeterminate. For `total` and `total_increasing` sensors this parameter has low effect — the end-of-silence marker is omitted so HA interpolates diagonally, which correctly reflects ongoing accumulation.

### Deadband parameters in practice

There are two mutually exclusive ways to control the deadband:

- **Fixed** — set `deadband` to a number in the source's own unit. For example, `deadband: 5` on a power sensor (W) means the filtered output only republishes once it has moved by at least 5 W from the last published value (see the integral correction rule above for the slow-drift exception). Once `deadband` is set, `deadband_tau_sigma` and `deadband_k_sigma` are ignored.
- **Adaptive (default)** — leave `deadband` unset. The integration estimates the variability (σ) of the filtered output with a moving average whose time constant grows up to `deadband_tau_sigma` seconds, and sets the effective deadband to `deadband_k_sigma × σ`. This is what "self-tuning" means in practice: a sensor whose system varies a lot gets a wider deadband automatically, a quiet one a narrower one, without you measuring anything by hand.

#### Large steps

σ includes every real movement within `deadband_tau_sigma`, steps included — by design, since that is what sets what the system usually does. Right after a large step, the deadband is therefore wider for a while: the next change is written a little later, and the integral correction still lets a lasting deviation through. If a source switches between levels often and you want each switch written sooner, lower `deadband_k_sigma` (1.5), or set a fixed `deadband` in the source's unit.

```yaml
# Fixed deadband: republish only on a change of at least 5 W
lowpass_dt:
  sensors:
    - source: sensor.oven_power_raw
      tau: 30
      deadband: 5

# Adaptive deadband, tuned to follow sooner a change in what the system usually does
# (useful when the amplitude of its variations itself changes a lot over the day)
lowpass_dt:
  sensors:
    - source: sensor.wind_speed_raw
      tau: 20
      deadband_tau_sigma: 600     # estimate σ over 10 minutes
      deadband_k_sigma: 1.5       # more sensitive than the 2.0 default

# Frequent large steps (battery power), each one wanted at once:
# no deadband, tau alone smooths
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
- Both the adaptive deadband and silence detection are learned from EMA statistics, and both have limits. On the value side, σ includes real movement by design, so a large step widens the deadband for a while — see [Large steps](#large-steps). On the timing side, a source publishing near Home Assistant's practical update ceiling with a short `tau` can make the silence threshold very tight — see the note under [Silence detection](#-silence-detection). There is currently no dedicated option to bypass silence detection the way `deadband: 0` bypasses the adaptive deadband.
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

### Adaptive Delta Encoding: writing what is significant

The deadband implements a form of **adaptive delta encoding** (also known as *send-on-delta* or *level-crossing sampling*): instead of writing every filtered value, the system

- applies a first-order low-pass filter (which removes the noise),
- estimates the amplitude (σ) of the filtered signal's own variations over `deadband_tau_sigma`,
- writes only when the filtered signal moves away from the last written value by more than `k·σ`.

The deadband has nothing to do with the measurement's uncertainty: it sorts what is **significant** from what is not. A variation within the amplitude of what the system does all the time is not significant — writing it would fill the Recorder for little — and is written only now and then; a change of state of the system, well beyond that amplitude, is written at once. With `k = 2`, a variation of the usual amplitude crosses the threshold rarely (about 5 % of the time if those variations were Gaussian), while a change of state crosses it at once.

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
