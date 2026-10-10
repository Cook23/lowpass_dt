"""The filter as a whole, against what it is for, in two regimes:

- normal: tau about 10 times the source's usual period (what the user is asked to set);
- slowed down: the source's period beyond tau. The low-pass no longer filters anything
  useful, but the output must stay sound: in the source's range, no overshoot, reaching
  the source's value, no flood of writes.

In the normal regime:
1. a first-order low-pass, whatever the sampling: the published values follow the exact
   first-order filter of the source (each value held until the next, as ZOH assumes),
   with intervals from a fraction of a second to many minutes, bursts and silences;
2. few writes: the Recorder gets far fewer states than the source sends;
3. a natural curve: the stored points, joined by straight lines, by a spline (third-party
   graphs: Chart.js 'tension' or 'monotone', d3, …) or held (Home Assistant's history),
   redraw the ideal filtered curve — no diagonal across a silence, thanks to the
   end-of-silence marker.

Everything runs in a real Home Assistant core, with a controlled clock.
"""
import bisect
import math
import random
from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from .conftest import setup_lowpass

SRC = "sensor.src"
OUT = "sensor.lp_src"
ATTRS = {"unit_of_measurement": "W", "state_class": "measurement"}


# ------------------------------------------------------------------ reference


def ideal(samples, tau, t):
    """Exact first-order low-pass at time t of the source, each sample held until the next
    one (zero-order hold), starting at the first sample's value."""
    y = samples[0][1]
    for (t0, x0), (t1, _) in zip(samples, samples[1:] + [(math.inf, None)]):
        if t0 >= t:
            break
        end = min(t1, t)
        y = x0 + (y - x0) * math.exp(-(end - t0) / tau)
    return y


def ideal_curve(samples, tau, times):
    """ideal() on many increasing times, in one pass."""
    out, y, k = [], samples[0][1], 0
    t_prev = samples[0][0]
    for t in times:
        while True:
            x0 = samples[k][1]
            t_next = samples[k + 1][0] if k + 1 < len(samples) else math.inf
            end = min(t_next, t)
            y = x0 + (y - x0) * math.exp(-(end - t_prev) / tau)
            t_prev = end
            if t_next <= t:
                k += 1
            else:
                break
        out.append(y)
    return out


def linear(points, t):
    """Straight lines between stored points (most graphs), held after the last one."""
    ts = [p[0] for p in points]
    i = bisect.bisect_right(ts, t) - 1
    if i < 0:
        return points[0][1]
    if i + 1 >= len(points):
        return points[i][1]
    (t0, v0), (t1, v1) = points[i], points[i + 1]
    return v0 if t1 == t0 else v0 + (v1 - v0) * (t - t0) / (t1 - t0)


def held(points, t):
    """Each stored value held until the next one (Home Assistant's history)."""
    ts = [p[0] for p in points]
    i = max(0, bisect.bisect_right(ts, t) - 1)
    return points[i][1]


def _dedupe(points):
    """Points at the same time: a graph draws the last one (the marker is followed by the
    new value at the same instant in a frozen-clock test)."""
    out = []
    for t, v in points:
        if out and t == out[-1][0]:
            out[-1] = (t, v)
        else:
            out.append((t, v))
    return out


def spline_tension(points, times, tension=0.4, width=1000.0, height=300.0):
    """Chart.js 'tension' curve (default 0.4 in many cards): a Bézier through each point,
    its control points along the neighbours' direction, scaled by the pixel distances.
    Times and values are mapped to a width × height chart first, as Chart.js does."""
    pts = _dedupe(points)
    t0, t1 = pts[0][0], max(times[-1], pts[-1][0])
    vs = [v for _, v in pts]
    vmin, vmax = min(vs), max(vs)
    sx = width / max(t1 - t0, 1e-9)
    sy = height / max(vmax - vmin, 1e-9)
    P = [((t - t0) * sx, (v - vmin) * sy) for t, v in pts]
    ctrl = []
    for i, cur in enumerate(P):
        prev = P[i - 1] if i > 0 else cur
        nxt = P[i + 1] if i + 1 < len(P) else cur
        d01 = math.dist(cur, prev)
        d12 = math.dist(nxt, cur)
        s01 = d01 / (d01 + d12) if d01 + d12 else 0.0
        s12 = d12 / (d01 + d12) if d01 + d12 else 0.0
        fa, fb = tension * s01, tension * s12
        ctrl.append((
            (cur[0] - fa * (nxt[0] - prev[0]), cur[1] - fa * (nxt[1] - prev[1])),
            (cur[0] + fb * (nxt[0] - prev[0]), cur[1] + fb * (nxt[1] - prev[1])),
        ))
    # each segment sampled as a polyline, then read at the asked times
    poly = []
    for i in range(len(P) - 1):
        a, b = P[i], P[i + 1]
        c1, c2 = ctrl[i][1], ctrl[i + 1][0]
        for k in range(32):
            u = k / 32
            x = (1 - u) ** 3 * a[0] + 3 * (1 - u) ** 2 * u * c1[0] + 3 * (1 - u) * u * u * c2[0] + u ** 3 * b[0]
            y = (1 - u) ** 3 * a[1] + 3 * (1 - u) ** 2 * u * c1[1] + 3 * (1 - u) * u * u * c2[1] + u ** 3 * b[1]
            poly.append((x / sx + t0, y / sy + vmin))
    poly.append((pts[-1][0], pts[-1][1]))
    poly.sort(key=lambda p: p[0])
    return [linear(poly, t) for t in times]


def spline_monotone(points, times):
    """Monotone cubic (Chart.js 'monotone', d3 curveMonotoneX): never beyond its points."""
    pts = _dedupe(points)
    n = len(pts)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    if n < 3:
        return [linear(pts, t) for t in times]
    d = [(ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]) for i in range(n - 1)]
    m = [d[0]] + [0.0 if d[i - 1] * d[i] <= 0 else (d[i - 1] + d[i]) / 2 for i in range(1, n - 1)] + [d[-1]]
    for i in range(n - 1):                         # Fritsch–Carlson
        if d[i] == 0:
            m[i] = m[i + 1] = 0.0
            continue
        a, b = m[i] / d[i], m[i + 1] / d[i]
        h = a * a + b * b
        if h > 9:
            k = 3 / math.sqrt(h)
            m[i], m[i + 1] = k * a * d[i], k * b * d[i]
    out = []
    for t in times:
        i = min(max(bisect.bisect_right(xs, t) - 1, 0), n - 2)
        if t >= xs[-1]:
            out.append(ys[-1])
            continue
        h = xs[i + 1] - xs[i]
        u = (t - xs[i]) / h
        h00, h10 = 2 * u ** 3 - 3 * u ** 2 + 1, u ** 3 - 2 * u ** 2 + u
        h01, h11 = -2 * u ** 3 + 3 * u ** 2, u ** 3 - u ** 2
        out.append(h00 * ys[i] + h10 * h * m[i] + h01 * ys[i + 1] + h11 * h * m[i + 1])
    return out


# ------------------------------------------------------------------ simulation


async def simulate(hass, freezer, samples, options, t_end):
    """Feeds the source with samples [(t, value)], runs the clock to t_end (firing the
    injection timers every second during gaps), and returns the stored states [(t, value)]."""
    er.async_get(hass).async_get_or_create("sensor", "lowpass_dt", "lowpass_dt::lp_src", suggested_object_id="lp_src")
    t0 = dt_util.utcnow()
    freezer.move_to(t0)
    hass.states.async_set(SRC, str(samples[0][1]), ATTRS)
    await setup_lowpass(hass, sensors=[{"source": SRC, **options}])

    stored = []

    def _listener(event):
        if event.data["entity_id"] == OUT and event.data["new_state"] is not None:
            try:
                v = float(event.data["new_state"].state)
            except ValueError:
                return
            stored.append(((dt_util.utcnow() - t0).total_seconds(), v))

    hass.bus.async_listen("state_changed", _listener)
    stored.append((0.0, float(hass.states.get(OUT).state)))

    async def at(t):
        now = t0 + timedelta(seconds=t)
        freezer.move_to(now)
        async_fire_time_changed(hass, now)
        await hass.async_block_till_done()

    t = 0.0
    for ts, x in samples[1:] + [(t_end, None)]:
        while ts - t > 1.0:            # a gap: let the timers run, second by second
            t += 1.0
            await at(t - 0.5)          # (between whole seconds, where the timers fall)
        t = ts
        if x is None:
            await at(t)
            break
        # the timers due before the sample, then the sample: a timer due at the very same
        # instant would otherwise fire before or after it depending on float rounding
        await at(t - 1e-3)
        freezer.move_to(t0 + timedelta(seconds=t))
        hass.states.async_set(SRC, repr(x), ATTRS)
        await hass.async_block_till_done()
    return stored


def irregular_samples(rng, t_end, signal):
    """Very irregular sampling: bursts (0.2 to 1 s), a usual rhythm of a few seconds,
    slow stretches (minutes) and a few long silences (20 to 60 minutes)."""
    out, t = [], 0.0
    while t < t_end:
        out.append((t, round(signal(t), 3)))
        r = rng.random()
        if r < 0.25:
            dt = rng.uniform(0.2, 1.0)
        elif r < 0.965:
            dt = rng.lognormvariate(math.log(4), 0.7)
        elif r < 0.997:
            dt = rng.uniform(60, 400)
        else:
            dt = rng.uniform(1200, 3600)
        t += dt
    return out


def report(name, samples, stored, tau, t_end, step=5.0):
    times = [k * step for k in range(int(t_end / step))]
    ref = ideal_curve(samples, tau, times)
    err_lin = [abs(linear(stored, t) - r) for t, r in zip(times, ref)]
    err_held = [abs(held(stored, t) - r) for t, r in zip(times, ref)]
    pub_err = [abs(v - ideal(samples, tau, t)) for t, v in stored[1:]]
    err_ten = [abs(a - r) for a, r in zip(spline_tension(stored, times), ref)]
    err_mono = [abs(a - r) for a, r in zip(spline_monotone(stored, times), ref)]
    stats = {
        "samples": len(samples),
        "writes": len(stored),
        "max_err_at_writes": max(pub_err) if pub_err else 0.0,
        "rms_err_linear": math.sqrt(sum(e * e for e in err_lin) / len(err_lin)),
        "max_err_linear": max(err_lin),
        "rms_err_held": math.sqrt(sum(e * e for e in err_held) / len(err_held)),
        "rms_err_tension": math.sqrt(sum(e * e for e in err_ten) / len(err_ten)),
        "max_err_tension": max(err_ten),
        "rms_err_monotone": math.sqrt(sum(e * e for e in err_mono) / len(err_mono)),
        "max_err_monotone": max(err_mono),
    }
    print(f"\n{name}: " + ", ".join(f"{k}={v:.4g}" if isinstance(v, float) else f"{k}={v}" for k, v in stats.items()))
    return stats


# ------------------------------------------------------------------ scenarios

def wave_samples(rng, t_end, period, burst=0.25, slow=0.0, silence=0.0, amplitude=5.0, noise=0.2):
    """A slow wave with noise, reported around `period` (log-normal), with bursts (0.2 to
    1 s), slow stretches (2 to 10 periods) and silences (20 to 60 min)."""
    out, t = [], 0.0
    while t < t_end:
        out.append((t, round(20 + amplitude * math.sin(2 * math.pi * t / 3600) + rng.gauss(0, noise), 3)))
        r = rng.random()
        if r < burst:
            dt = rng.uniform(0.2, 1.0)
        elif r < 1 - slow - silence:
            dt = rng.lognormvariate(math.log(period), 0.5)
        elif r < 1 - silence:
            dt = rng.uniform(2 * period, 10 * period)
        else:
            dt = rng.uniform(1200, 3600)
        t += dt
    return out


def sound(samples, stored):
    """Every stored value within the source's range (no overshoot)."""
    lo = min(x for _, x in samples)
    hi = max(x for _, x in samples)
    return all(lo - 1e-6 <= v <= hi + 1e-6 for _, v in stored)


# ------------------------------------------------------------------ normal regime


async def test_normal_dense_noisy_source_spares_the_recorder(hass: HomeAssistant, freezer):
    """Reports every 2 s or so (bursts included), tau = 20 s, the default adaptive deadband,
    3 hours of a drifting noisy signal: few writes, and their curve on the filtered signal."""
    rng = random.Random(3)
    tau, t_end = 20.0, 3 * 3600.0
    samples = wave_samples(rng, t_end, period=2.0, burst=0.3, amplitude=2.0, noise=0.3)
    stored = await simulate(hass, freezer, samples, {"tau": tau}, t_end)
    s = report("normal, dense noisy", samples, stored, tau, t_end)
    assert s["writes"] < s["samples"] / 10
    assert s["rms_err_linear"] < 0.3               # well under the noise (σ = 0.3)
    assert sound(samples, stored)


async def test_normal_irregular_sampling_is_first_order(hass: HomeAssistant, freezer):
    """Reports every 4 s or so, with bursts, slow stretches and silences, tau = 40 s,
    deadband 0.1: each written value is the exact first-order filter of the source, the
    Recorder is spared, and the stored points redraw the filter by straight lines or
    splines."""
    rng = random.Random(1)
    tau, t_end = 40.0, 3 * 3600.0
    samples = wave_samples(rng, t_end, period=4.0, slow=0.03, silence=0.002)
    stored = await simulate(hass, freezer, samples, {"tau": tau, "deadband": 0.1}, t_end)
    s = report("normal, irregular", samples, stored, tau, t_end)
    assert s["writes"] < s["samples"] / 3
    assert s["max_err_at_writes"] < 2 * 0.1
    assert s["rms_err_linear"] < 0.1
    assert s["rms_err_monotone"] < 0.1
    assert s["rms_err_tension"] < 0.1
    assert sound(samples, stored)


async def test_normal_silence_is_drawn_flat_then_resumes(hass: HomeAssistant, freezer):
    """Reports every 10 s, tau = 100 s; an hour of silence, then a new level: the stored
    curve is flat over the silence (no diagonal), then rises, whatever draws it."""
    rng = random.Random(2)
    tau, t_end = 100.0, 3 * 3600.0
    samples = [(10.0 * k, round(30 + rng.gauss(0, 0.05), 3)) for k in range(0, 360)]
    samples += [(7200.0 + 10.0 * k, round(50 + rng.gauss(0, 0.05), 3)) for k in range(0, 360)]
    stored = await simulate(hass, freezer, samples, {"tau": tau, "deadband": 0.2}, t_end)
    s = report("normal, silence", samples, stored, tau, t_end)
    lin = [linear(stored, t) for t in range(3600 + 1200, 7200 - 10, 30)]
    assert max(abs(v - 30) for v in lin) < 0.3
    assert s["max_err_linear"] < 1.0
    assert s["max_err_monotone"] < 1.0
    assert sound(samples, stored)


async def test_normal_silence_resume_redrawn_by_tension_spline(hass: HomeAssistant, freezer):
    """The same, drawn by Chart.js 'tension: 0.4': where the output starts rising again, the
    spline rounds the corner; it stays within 1 (5 % of the step)."""
    rng = random.Random(2)
    tau, t_end = 100.0, 3 * 3600.0
    samples = [(10.0 * k, round(30 + rng.gauss(0, 0.05), 3)) for k in range(0, 360)]
    samples += [(7200.0 + 10.0 * k, round(50 + rng.gauss(0, 0.05), 3)) for k in range(0, 360)]
    stored = await simulate(hass, freezer, samples, {"tau": tau, "deadband": 0.2}, t_end)
    s = report("normal, silence (tension)", samples, stored, tau, t_end)
    assert s["max_err_tension"] < 1.0


async def test_normal_sparse_spikes_weighted_by_their_duration(hass: HomeAssistant, freezer):
    """Issue #4: a 45 W baseline reported every 30 s, tau = 300 s, a 50 W spike of 1 s
    every 5 min. The output, averaged over time, is the source's time-average (45.017 W),
    not the spike counted for the whole preceding interval."""
    tau, t_end = 300.0, 4 * 3600.0
    samples, t = [], 0.0
    while t < t_end:
        for k in range(10):
            samples.append((t + 30 * k, 45.0 + 0.001 * (k % 2)))
        samples.append((t + 275.0, 50.0))
        samples.append((t + 276.0, 45.0005))
        t += 300.0
    samples.sort()
    stored = await simulate(hass, freezer, samples, {"tau": tau, "deadband": 0.01, "max_rate_dt": 1}, t_end)
    report("normal, spikes", samples, stored, tau, t_end)
    times = [k * 5.0 for k in range(int(t_end / 2 / 5), int(t_end / 5))]
    mean_out = sum(held(stored, t) for t in times) / len(times)
    print(f"time-average of the output: {mean_out:.4f} (source: {45 + 5 / 300:.4f})")
    assert abs(mean_out - (45 + 5 / 300)) < 0.01


# ------------------------------------------------------------------ power consumption
# What the user wants (default adaptive deadband): no static error — a source that holds
# still ends up published exactly; a change of state of the system (a big consumer
# switching) written at once; otherwise, while the load "lives", a point now and then.


def power_samples(rng, t_end, consumers, period=5.0):
    """A household base load that lives (300 W, slow wander, ±30 W jitter), reported every
    5 s or so, plus consumers: [(watts, t_on, t_off)]."""
    out, t, wander = [], 0.0, 0.0
    while t < t_end:
        wander = 0.98 * wander + rng.gauss(0, 8)
        extra = sum(w for w, a, b in consumers if a <= t < b)
        out.append((t, round(300 + wander + rng.gauss(0, 30) + extra, 1)))
        t += rng.lognormvariate(math.log(period), 0.4)
    return out


def no_static_error(hass, stored, samples):
    """After the source stopped changing, the output is its last value, to the published
    decimals (derived from the deadband when `round` is not set)."""
    ent = hass.data["entity_components"]["sensor"].get_entity(OUT)
    assert stored[-1][1] == round(samples[-1][1], ent.publisher.decimals)


def detection_delay(stored, t_edge, size):
    """Time from a switch to the first write that moved by a quarter of it."""
    before = held(stored, t_edge - 1)
    return next((t - t_edge for t, v in stored if t >= t_edge and abs(v - before) >= 0.25 * size), math.inf)


async def test_power_switches_written_at_once_otherwise_a_point_now_and_then(hass: HomeAssistant, freezer):
    """tau = 30 s. A 600 W consumer, then a 2000 W one for an hour, then 600 W again 30 min
    after it (σ still includes the big step), then the source stops changing."""
    rng = random.Random(5)
    tau, t_end = 30.0, 7 * 3600.0
    consumers = [(600.0, 1.5 * 3600, 2 * 3600), (2000.0, 3 * 3600, 4 * 3600), (600.0, 4.5 * 3600, 5 * 3600)]
    samples = power_samples(rng, t_end - 3600, consumers)
    stored = await simulate(hass, freezer, samples, {"tau": tau}, t_end)
    delays = {f"{w:.0f} W {'on' if k == 0 else 'off'} at {t / 3600:.2f} h": detection_delay(stored, t, w)
              for w, a, b in consumers for k, t in enumerate((a, b))}
    quiet = sum(1 for t, _ in stored if 1800 <= t < 3600) * 2
    print("\npower:", {k: (round(v) if v != math.inf else "never") for k, v in delays.items()}, f"writes/h while it lives: {quiet}")
    # the filter itself needs 0.29 tau to move by a quarter of a step; the deadband, raised
    # by the steps σ includes, adds to it: allow 3 tau
    assert all(d <= 3 * tau for d in delays.values()), delays
    assert quiet <= 120                                    # a point now and then
    no_static_error(hass, stored, samples)


async def test_power_cycling_consumer_written_each_time(hass: HomeAssistant, freezer):
    """A 2000 W consumer cycling 10 min on / 10 min off (a thermostat): σ then includes the
    steps themselves and the deadband rises; each switch is still written within 3 tau."""
    rng = random.Random(5)
    tau, t_end = 30.0, 6 * 3600.0
    consumers = [(2000.0, 3600.0 + k * 1200.0, 3600.0 + k * 1200.0 + 600.0) for k in range(12)]
    samples = power_samples(rng, t_end - 3600, consumers)
    stored = await simulate(hass, freezer, samples, {"tau": tau}, t_end)
    delays = [detection_delay(stored, t, 2000.0) for _, a, b in consumers for t in (a, b)]
    print("\ncycling:", [round(d) for d in delays])
    assert max(delays) <= 3 * tau
    no_static_error(hass, stored, samples)


# ------------------------------------------------------------------ slow sources (weather)


async def test_slow_source_new_value_moves_output_at_once(hass: HomeAssistant, freezer):
    """A weather sensor reporting every 10 min or so, tau = 100 min: a new value starts
    moving the output at once, along its known course, not one sample (10 min) later —
    and the curve stays the first-order filter, with few writes."""
    rng = random.Random(6)
    tau, t_end = 6000.0, 8 * 3600.0
    samples, t = [], 0.0
    while t < t_end - 3600:
        level = 10.0 if t < 2 * 3600 else 15.0
        samples.append((t, round(level + rng.gauss(0, 0.05), 2)))
        t += rng.uniform(540, 660)
    t_new = next(t for t, _ in samples if t >= 2 * 3600)
    stored = await simulate(hass, freezer, samples, {"tau": tau, "deadband": 0.1}, t_end)
    s = report("weather", samples, stored, tau, t_end)
    delay = next(t for t, v in stored if t > t_new and v > held(stored, t_new) + 0.05) - t_new
    # the ideal output itself needs this long to move by the deadband (0.1 of a 5° step)
    t_cross = -tau * math.log(1 - 0.1 / 5.0)
    step = max(1.0, 10.0, tau / 100)                  # the tracking step (max_rate_dt 10 s)
    print(f"first move after the new value: {delay:.0f} s (ideal crosses the deadband at {t_cross:.0f} s)")
    assert delay <= t_cross + step + 1               # not ~600 s (the next sample)
    assert s["max_err_at_writes"] < 2 * 0.1
    assert s["max_err_linear"] < 0.5
    assert s["writes"] < 150


# ------------------------------------------------------------------ slowed-down regime


async def test_slowed_source_step_stays_sound(hass: HomeAssistant, freezer):
    """A step seen by a source that reports every 10 minutes, tau = 2 minutes (set for a
    source 10 times faster): nothing useful to filter, but the output stays within the
    source's range, rises without overshoot, and reaches the new value."""
    tau, t_end = 120.0, 3600.0
    samples = [(0.0, 0.0), (600.0, 100.0)] + [(600.0 * k + 0.5 * (k % 2), 100.0 + 0.01 * (k % 2)) for k in range(2, 6)]
    stored = await simulate(hass, freezer, samples, {"tau": tau, "deadband": 0.5, "max_rate_dt": 1}, t_end)
    report("slowed, step", samples, stored, tau, t_end)
    assert sound(samples, stored)
    rise = [v for t, v in stored if 600 <= t < 1200]
    assert rise == sorted(rise)
    assert abs(stored[-1][1] - 100.0) < 0.5


async def test_slowed_source_step_is_first_order(hass: HomeAssistant, freezer):
    """The same step: once the new value is known, the written values follow
    1 − exp(−t/tau), not a slower curve."""
    tau, t_end = 120.0, 3600.0
    samples = [(0.0, 0.0), (600.0, 100.0)] + [(600.0 * k + 0.5 * (k % 2), 100.0 + 0.01 * (k % 2)) for k in range(2, 6)]
    stored = await simulate(hass, freezer, samples, {"tau": tau, "deadband": 0.5, "max_rate_dt": 1}, t_end)
    s = report("slowed, step (first order)", samples, stored, tau, t_end)
    assert s["max_err_at_writes"] < 2 * 0.5


async def test_source_slowing_down_stays_sound(hass: HomeAssistant, freezer):
    """A source set for tau = 40 s (every 4 s), that slows down to one value every 1 to
    10 minutes for an hour, then comes back: the output stays sound and follows it."""
    rng = random.Random(4)
    tau, t_end = 40.0, 3 * 3600.0
    samples, t = [], 0.0
    while t < t_end:
        samples.append((t, round(20 + 5 * math.sin(2 * math.pi * t / 3600) + rng.gauss(0, 0.2), 3)))
        t += rng.uniform(60, 600) if 3600 <= t < 7200 else rng.lognormvariate(math.log(4), 0.5)
    stored = await simulate(hass, freezer, samples, {"tau": tau, "deadband": 0.1}, t_end)
    s = report("slowing down", samples, stored, tau, t_end)
    assert sound(samples, stored)
    assert s["rms_err_linear"] < 0.5


# A battery's power, at rest at 12 W between short bursts every 2 s, tau only 2.5 times
# the source's period (discussion #6, real data): not what the filter is for, but at each
# rest the output must reach 12 W during the silence, not stay at the last burst's value
# (up to 1.3.17, the injection stopped after its first sample).
BATTERY_REST = [(0.0, 12), (68.271, 78), (69.275, 462), (71.292, 297), (73.31, 156), (87.416, 147), (89.43, 153), (91.445, 150), (93.461, 147), (97.488, 12), (1522.07, 1032), (1523.078, 1029), (1524.087, 456), (1526.1, 288), (1528.116, 156), (1540.189, 150), (1542.2, 153), (1544.217, 141), (1546.227, 153), (1548.235, 156), (1550.243, 159), (1554.266, 12)]


@pytest.mark.parametrize("options", [{"tau": 5, "deadband": 0}, {"tau": 5}])
async def test_edge_battery_at_rest_reaches_its_value_in_each_silence(hass: HomeAssistant, freezer, options):
    stored = await simulate(hass, freezer, BATTERY_REST, options, 1700)
    for t_rest in (97.488, 1554.266):  # the source drops to 12 W, then is silent
        after = [v for t, v in stored if t_rest < t < t_rest + 60]
        assert after and after[-1] == 12.0
        assert all(12.0 <= v <= 1032 for v in after)
    if "deadband" in options:  # (every write is the exact first-order value)
        assert all(abs(v - ideal(BATTERY_REST, 5, t)) < 0.01 for t, v in stored[1:])
