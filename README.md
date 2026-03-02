
# ⚠️ Experimental Component – Read Before Using

> **Tested by exactly one person: me.**
>
> This integration is experimental.
>
> It works well in my environment.
> It might break yours.
>
> Bugs can happen.
> Edge cases may exist.
> Math can go wrong.
> Silence detection might misbehave.
>
> 👉 **Back up your Home Assistant before installing.**
>
> If something explodes, it's on you.
>
> You have been warned.

> 👉 **This component may generate a large number of warning messages in the logs.**
>
> It is still in early development and initial validation phase.
> Extensive logging is intentional and used to validate mathematical behavior,
> edge cases, and stability under real-world conditions.
>
> Log verbosity will be reduced in future stable versions.

---

# Lowpass DT – Deterministic Time-Aware Filter for Home Assistant

![HACS](https://img.shields.io/badge/HACS-Custom-blue.svg)
![Experimental](https://img.shields.io/badge/status-experimental-orange)
![Math Driven](https://img.shields.io/badge/design-math%20driven-black)

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

This component does.

---

## 🧠 What Makes It Different

### ✔ Time-aware integration (Δt-based)

Handles irregular update intervals correctly:

```
alpha = dt / (tau + dt)
y = y + alpha * (x - y)
```

- No sample-rate dependency.
- No overshoot. No instability.
- Acts as a true first-order low-pass filter.

---

### ✔ Silence detection

When a sensor stops publishing for longer than:

```
dt_silence = mean(dt) + 3σ
```

- Mimics the behavior of a sensor sampled at a constant rate.
- Synthetic updates are injected using the last known real value until the filter converges smoothly.
- The final filter output always equals the last real value received before silence.

No frozen fake values.

---

### ✔ Adaptive deadband

Optional adaptive deadband:

```
deadband = k × sigma(filtered_signal)
```

- Keeps only statistically meaningful changes.
- Eliminates micro-noise.
- Automatically scales with signal variability.

---

### ✔ Recorder-friendly

- Suppresses insignificant updates.
- Reduces database growth.
- Keeps long-term statistics meaningful.
- Designed for high-frequency sensors.

---

## ⚙ Configuration

### ⚙ Explicit Configuration Example

You can configure each sensor:

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

Except for the `source` parameter, most parameters are optional. Default values are generally sufficient.
Even `tau` is usually not required. The default value of 60 seconds is generally adequate.

---

### ⚙ Batch Configuration Example

Or you can configure multiple sensors in batch mode:

```yaml
patterns:
  - match: "sensor.temperature_*"
    tau: 60
```

No per-sensor tuning is required because parameters adapt automatically.
Even `tau` is usually not required. The default value of 60 seconds is generally sufficient.

---

### ⚙ How to Fine-Tune Parameters Beyond Default Values

- Disable the deadband by setting `deadband: 0`
- Plot historical curves of:
  - the source measurement
  - the filtered measurement
- Adjust `tau` to filter out unwanted noise while preserving meaningful variations
- Then you have two options:
  - define a fixed deadband value
  - or return to automatic deadband mode

If you choose automatic deadband:

- Remove the `deadband` parameter
- Wait approximately `300 × tau` for stabilization
  - if `tau = 1 minute`, wait at least 5 hours
  - if `tau = 1 hour`, wait at least 15 days
- If needed, adjust `deadband_k_sigma`:
  - Increase it to make the filter less sensitive
  - Decrease it to make the filter more sensitive

---

### ❗ Deadband Formula

The implementation uses an integral deadband formula:

```
e = y - y_last_published
i = i + (e * dt) / tau   (only if |e| < D)
Publish if |e| >= D OR |i| >= D
```

This means that a small variation, smaller than the deadband threshold, will still be recorded if it persists long enough.

The time constant of this integral action is the same as the main low-pass filter `tau`.

---

### ❗ Fine-Tuning in Explicit or Batch Configuration Mode

Fine-tuning is possible in both Explicit and Batch modes. In Batch mode, the parameters apply to all sensors in the batch and therefore should be similar.

---

## 📦 Installation (HACS)

1. Add this repository as a **Custom Repository** in HACS
2. Category: **Integration**
3. Install
4. Restart Home Assistant

---

## 📘 Parameters & Default Values

### Explicit Mode

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| source | string | required | Source sensor entity_id |
| tau | float | 60.0 | Low-pass tau time constant in seconds |

### Pattern Mode

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| match | string | required | Source sensor match string |
| tau | float | 60.0 | Low-pass tau time constant in seconds |

A match string should avoid matching already filtered entities.
A prefix is added to the generated entity_id to prevent this.
Recursion is automatically blocked if a misconfigured match string matches filtered entities.
To prevent misconfiguration from creating thousands of entities, creation is limited to 100 entities per match string.

---

### Naming

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| prefix | string | "lp_" | Prefix for generated entity_id |
| suffix | string | "(Filtered)" | Suffix added to the friendly name |
| name | string | None | Explicit friendly name (disables prefix/suffix) |
| unique_id | string | auto-generated | Optional unique_id seed (explicit sensors only) |

---

### Fixed Deadband Mode

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| deadband | float | None | Deadband to limit output rate |

---

### Adaptive Deadband Mode

Adaptive deadband (default when `deadband` is not set):

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| deadband_tau_sigma | float | max(100 × tau, 10) | Period over which deadband is estimated |
| deadband_k_sigma | float | 3.0 | Deviation multiplier for deadband threshold |

Effective deadband = `k × sigma(filtered_signal)`

---

### Rate Control

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| min_rate_dt | float | 3600 | Maximum interval between publishes (seconds) |
| max_rate_dt | float | 10 | Minimum interval between publishes (rate limiter) |

`min_rate_dt` prevents very long periods without states recorded in the Recorder.
It can improve history graphs and dashboard cards.

`max_rate_dt` should be considered a last line of defense against flooding the Recorder.
It should almost never be reached, except in cases of misconfiguration.

---

### Rounding

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| round | int | auto-derived | Avoid non-significant digits |

---

## 🧩 Key Features

| Feature | Supported |
|----------|-----------|
| Δt-aware filtering | ✅ |
| Silence detection | ✅ |
| Synthetic injection | ✅ |
| Adaptive deadband | ✅ |
| Fixed deadband | ✅ |
| Rate limiting | ✅ |
| HA-native restore | ✅ |
| Recorder optimization | ✅ |
| Batch pattern mode | ✅ |

---

## 🏗 Architecture

- **LowpassCore** → Pure math engine
- **TauInjector** → Silence detection & injection
- **Publisher** → Home Assistant state exposure
- **HA-native restore** → Clean persistence

No polling.
Fully event-driven.

---

## ⚠ Known Limitations

- No ConfigFlow UI yet
- Not reviewed for HA Core inclusion
- Experimental default tuning
- Edge cases may exist

---

## 📈 Performance

- O(1) per update
- No background loops
- Injection active only during silence
- Safe for large sensor sets

---

## 📜 License

MIT

---

## 👤 Author

Built to solve a real problem:

Filtering real-world asynchronous sensors without lying to the math.

If you have experienced incorrect frozen values at the output of a filter,
or seen filtered values behave erratically when the sensor reporting rate changes,
this integration is for you.

This is my first Home Assistant integration and my first software development project in Python.
I come from the industrial automation and process control world, where C is king.

So yes, errors and mistakes are absolutely possible. Please be kind.

---

## References

### Adaptive Delta Encoding for Gaussian Noise

This filter implements a form of **adaptive delta encoding** (also known as *send-on-delta* or *level-crossing sampling*) optimized for Gaussian noise environments.

Instead of transmitting every sampled value, the system:

- Applies a first-order low-pass filter
- Dynamically estimates the noise level (σ)
- Publishes only when the filtered signal deviates from the last published value by more than `k·σ`

When `k = 3`, the probability that pure Gaussian noise triggers a transmission is approximately **0.27%**, making the encoder statistically near-optimal for suppressing noise-induced events while preserving meaningful signal variations.

This approach is closely related to:

- **Adaptive Delta Modulation (ADM)**
- **Level-Crossing Sampling**
- **Send-on-Delta transmission schemes**
- Statistical thresholding based on **Rice’s level-crossing theory**

#### Bibliography

- Rice, S. O. (1944–1945). *Mathematical Analysis of Random Noise*. Bell System Technical Journal.
- Proakis, J. G., & Salehi, M. *Digital Communications*. McGraw-Hill.
- Gubner, J. A. *Probability and Random Processes for Electrical and Computer Engineers*. Cambridge University Press.
