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

No overshoot.  
No instability.  
No sample-rate dependency.

---

### ✔ Silence detection

When a sensor stops publishing:

```
dt_silence = mean(dt) + 3σ
```

- Synthetic updates are injected.
- The filter converges smoothly.
- Injection stops immediately when the sensor resumes.

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

### ✔ Batch-friendly

You can apply it to many sensors:

```yaml
patterns:
  - match: "sensor.temperature_*"
    tau: 60
```

No per-sensor tuning required.  
Parameters auto-adapt.

---

## 📦 Installation (HACS)

1. Add this repository as a **Custom Repository** in HACS  
2. Category: **Integration**  
3. Install  
4. Restart Home Assistant  

---

## ⚙ Configuration Example

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

If you understand why frozen values are wrong,  
this integration is for you.