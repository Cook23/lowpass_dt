# ⚠️ Experimental Component – Read Before Using

> **Tested by exactly one person: me.**
>
> This integration is experimental.
>
> It works well in my environment.
> It might break yours.
>
> Bugs can happen.
> Edge cases can exist.
> Math can go wrong.
> Silence detection might misbehave.
>
> 👉 **Backup your Home Assistant before installing.**
>
> If something explodes, it's on you.
>
> You have been warned.

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

- Prevent useless state updates
- Avoid flooding the Recorder database
- Handle sensors that:
  - Talk whenever they want
  - Stop talking without warning
  - Resume at random intervals
- Work in batch mode without per-sensor tuning
- Automatically compute statistical parameters
- Preserve signal integrity
- Avoid false frozen values during silence

---

## ❗ Why This Exists

Home Assistant already has filters.

But:

- Standard filters are not Δt-aware.
- Most filters assume regular sampling.
- During long silence, many filters simply freeze the last value.
- Frozen values are mathematically wrong.
- Frozen values pollute the Recorder with false states.
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
- Act as a real 1st order low-pass filter

---

### ✔ Silence detection

When a sensor stops publishing for a time greater than:

```
dt_silence = mean(dt) + 3σ
```

- Mimics the real behavior of a sensor scanned at a constant rate.
- Synthetic updates are injected with last real known value until filter converges smoothly.
- Last value at filter output is always last value received from sensor before it was silent.

No frozen fake values.

---

### ✔ Adaptive deadband

Optional adaptive deadband:

```
deadband = k × sigma(filtered_signal)
```

- Keeps only statistically meaningful changes
- Eliminates micro-noise
- Automatically scales with signal variability

---

### ✔ Recorder-friendly

- Suppresses insignificant updates
- Reduces database growth
- Keeps long-term statistics meaningful
- Designed for high-frequency sensors

---

## ⚙ Configuration

### ⚙ Batch Configuration Example

You can apply it to many sensors as batch:

```yaml
patterns:
  - match: "sensor.temperature_*"
    tau: 60
```

No per-sensor tuning required. Parameters auto-adapt.
Even "tau" is not really needed. The default value 60 sec is generally fine.


### ⚙ Explicit Configuration Example

Or you can apply it to each sensors:

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

Except "source" parameters are not needed. Defaults values are generally fine.

---

## 📦 Installation (HACS)

1. Add this repository as a **Custom Repository** in HACS  
2. Category: **Integration**  
3. Install  
4. Restart Home Assistant  

---

## 📘 Parameters & Defaults values

### Explicit mode

| Parameter | Type | Default | Description |
|----------|-----------|----------|-----------|
| source | string | required | Source sensor entity_id |
| tau | float | 60.0 | Low-pass tau time constant in seconds |

### Pattern Mode

| Parameter | Type | Default | Description |
|----------|-----------|----------|-----------|
| match | string | required | Source sensor match string |
| tau | float | 60.0 | Low-pass tau time constant in seconds |

A match string should avoid matching already filtered entities.
A prefix is added to the filtered entity_id to prevent this.
Recursion is automatically blocked if a misconfigured match string matches filtered entities.
To prevent misconfiguration from creating thousands of entities, creation is limited to 100 entities per match string.


### Naming

| Parameter | Type | Default | Description |
|----------|-----------|----------|-----------|
| prefix | string | "lp\_" | Prefix for generated entity_id |
| suffix | string | "(Filtered)" | Suffix added to friendly name |
| name | string | None | Explicit friendly name (disables prefix/suffix) |
| unique_id | string | auto-generated | Optional unique_id seed (explicit sensors only) |


### Fixed Deadband Mode

| Parameter | Type | Default | Description |
|----------|-----------|----------|-----------|
| deadband | float | None | deadband to limit output rate |

### Adaptive Deadband Mode

Adaptive deadband (default when deadband is not set):

| Parameter | Type | Default | Description |
|----------|-----------|----------|-----------|
| deadband_tau_sigma | float | max(100 × tau, 10) | deadband is estimated on this period |
| deadband_k_sigma | float | 2.0 | deadband is estimated inside this deviation |

Effective deadband = k × sigma(filtered_signal)


### Rate Control

| Parameter | Type | Default | Description |
|----------|-----------|----------|-----------|
| min_rate_dt | float | 3600 | Maximum interval between publishes (seconds) |
| max_rate_dt | float | 10 | Minimum interval between publishes (rate limiter) |

min_rate_dt prevents very long periods with no states recorded in the Recorder.
It can improve history graphs and cards.

max_rate_dt should be considered a last line of defense against flooding the Recorder.
It should almost never be reached, except in case of misconfiguration


### Rounding

| Parameter | Type | Default | Description |
|----------|-----------|----------|-----------|
|  round | int | auto-derived | Avoid non-significant digit |

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

- **LowpassCore** → pure math engine  
- **TauInjector** → silence detection & injection  
- **Publisher** → HA exposure  
- **HA-native restore** → clean persistence  

No polling.  
Fully event-driven.

---

## ⚠ Known Limitations

- No ConfigFlow UI yet
- Not reviewed for HA Core inclusion
- Experimental tuning defaults
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

If you have already experienced incorrect frozen values at the output of a filter,
if you have already seen filtered values behave erratically when the sensor reporting rate changes,
this integration is for you.

This is my first Home Assistant integration and my first development project in Python.
I come from the industrial automation and process control world, where C is king.

So yes, errors and mistakes are absolutely possible. Please be kind.
