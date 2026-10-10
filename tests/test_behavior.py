"""The filter as a whole, against what it is for:

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
            await at(t)
        t = ts
        await at(t)
        if x is not None:
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


async def test_dense_noisy_source_spares_the_recorder(hass: HomeAssistant, freezer):
    """A noisy source reporting every 2 s or so (bursts included) for 3 hours, a slow
    drift, the default adaptive deadband: few writes, and the curve they draw stays on
    the filtered signal."""
    rng = random.Random(3)
    tau, t_end = 60.0, 3 * 3600.0
    signal = lambda t: 20 + 2 * math.sin(2 * math.pi * t / 7200) + rng.gauss(0, 0.3)
    samples, t = [], 0.0
    while t < t_end:
        samples.append((t, round(signal(t), 3)))
        t += rng.uniform(0.2, 1.0) if rng.random() < 0.3 else rng.lognormvariate(math.log(2), 0.5)
    stored = await simulate(hass, freezer, samples, {"tau": tau}, t_end)
    s = report("dense noisy", samples, stored, tau, t_end)
    assert s["writes"] < s["samples"] / 10
    assert s["rms_err_linear"] < 0.3               # well under the noise (σ = 0.3)


@pytest.mark.xfail(strict=True, reason="alpha = dt / (tau + dt) is not the exact first order: alpha = 1 - exp(-dt / tau) planned (BRANCH.md)")
async def test_irregular_sampling_follows_first_order(hass: HomeAssistant, freezer):
    """The same: each written value is the exact first-order filter of the source (within
    the deadband), and the stored points joined by straight lines redraw it."""
    rng = random.Random(1)
    tau, t_end = 300.0, 3 * 3600.0
    signal = lambda t: 20 + 5 * math.sin(2 * math.pi * t / 3600) + rng.gauss(0, 0.2)
    samples = irregular_samples(rng, t_end, signal)
    stored = await simulate(hass, freezer, samples, {"tau": tau, "deadband": 0.1}, t_end)
    s = report("irregular", samples, stored, tau, t_end)
    assert s["max_err_at_writes"] < 2 * 0.1        # each written value: the first-order filter
    assert s["rms_err_linear"] < 0.1               # the curve redrawn by straight lines,
    assert s["rms_err_monotone"] < 0.1             # by a monotone spline,
    assert s["rms_err_tension"] < 0.1              # by a 'tension' spline


@pytest.mark.xfail(strict=True, reason="alpha = dt / (tau + dt) is not the exact first order: alpha = 1 - exp(-dt / tau) planned (BRANCH.md)")
async def test_slow_source_step_response_is_first_order(hass: HomeAssistant, freezer):
    """A step seen by a source that reports every 10 minutes, tau = 2 minutes: once the
    new value is known, the output follows 1 − exp(−t/tau), not a coarser curve."""
    tau, t_end = 120.0, 3600.0
    samples = [(0.0, 0.0), (600.0, 100.0)] + [(600.0 * k + 0.5 * (k % 2), 100.0 + 0.01 * (k % 2)) for k in range(2, 6)]
    stored = await simulate(hass, freezer, samples, {"tau": tau, "deadband": 0.5, "max_rate_dt": 1}, t_end)
    s = report("slow step", samples, stored, tau, t_end)
    assert s["max_err_at_writes"] < 2 * 0.5


@pytest.mark.xfail(strict=True, reason="points published every tau at best while the output moves: straight lines between them cut under the exponential (pacing by the deadband proposed)")
async def test_slow_source_step_is_redrawn_by_straight_lines(hass: HomeAssistant, freezer):
    """The same step: the stored points, joined by straight lines, redraw the exponential
    within a few deadbands — the points must be close enough where the output moves."""
    tau, t_end = 120.0, 3600.0
    samples = [(0.0, 0.0), (600.0, 100.0)] + [(600.0 * k + 0.5 * (k % 2), 100.0 + 0.01 * (k % 2)) for k in range(2, 6)]
    stored = await simulate(hass, freezer, samples, {"tau": tau, "deadband": 0.5, "max_rate_dt": 1}, t_end)
    s = report("slow step (redrawn)", samples, stored, tau, t_end)
    assert s["max_err_linear"] < 3 * 0.5


async def test_silence_is_drawn_flat_then_resumes(hass: HomeAssistant, freezer):
    """Regular samples, an hour of silence, then a new level: the stored curve is flat over
    the silence (no diagonal), with straight lines as with steps."""
    rng = random.Random(2)
    tau, t_end = 60.0, 3 * 3600.0
    samples = [(10.0 * k, round(30 + rng.gauss(0, 0.05), 3)) for k in range(0, 360)]          # 1 h at ~30
    samples += [(3600.0 + 3600.0 + 10.0 * k, round(50 + rng.gauss(0, 0.05), 3)) for k in range(0, 360)]  # after 1 h
    stored = await simulate(hass, freezer, samples, {"tau": tau, "deadband": 0.2}, t_end)
    s = report("silence", samples, stored, tau, t_end)
    # during the silence (well after convergence), the straight lines stay on the plateau
    lin = [linear(stored, t) for t in range(3600 + 600, 7200 - 10, 30)]
    assert max(abs(v - 30) for v in lin) < 0.3
    assert s["max_err_at_writes"] < 0.6
    assert s["max_err_monotone"] < 0.7            # a monotone spline redraws it as well


@pytest.mark.xfail(strict=True, reason="a 'tension' spline rounds the corner where the output starts moving again: with points ~20 s apart (max_rate_dt), it bows away from the exponential (denser points after a corner proposed)")
async def test_silence_resume_redrawn_by_tension_spline(hass: HomeAssistant, freezer):
    """The same silence, drawn by Chart.js with 'tension: 0.4' (many cards): the curve
    should not bow away from the filtered output where it starts moving again."""
    rng = random.Random(2)
    tau, t_end = 60.0, 3 * 3600.0
    samples = [(10.0 * k, round(30 + rng.gauss(0, 0.05), 3)) for k in range(0, 360)]
    samples += [(3600.0 + 3600.0 + 10.0 * k, round(50 + rng.gauss(0, 0.05), 3)) for k in range(0, 360)]
    stored = await simulate(hass, freezer, samples, {"tau": tau, "deadband": 0.2}, t_end)
    s = report("silence (tension)", samples, stored, tau, t_end)
    assert s["max_err_tension"] < 1.0


async def test_sparse_spikes_weighted_by_their_duration(hass: HomeAssistant, freezer):
    """Issue #4: a 45 W baseline reported every 30 s, a 50 W spike of 1 s every 5 min. The
    filtered output, averaged over time, is the source's time-average (45.017 W), not
    the spike counted for the whole preceding interval."""
    tau, t_end = 60.0, 4 * 3600.0
    samples, t = [], 0.0
    while t < t_end:
        for k in range(10):
            samples.append((t + 30 * k, 45.0 + 0.001 * (k % 2)))
        samples.append((t + 275.0, 50.0))
        samples.append((t + 276.0, 45.0005))
        t += 300.0
    samples.sort()
    stored = await simulate(hass, freezer, samples, {"tau": tau, "deadband": 0.01, "max_rate_dt": 1}, t_end)
    s = report("spikes", samples, stored, tau, t_end)
    times = [k * 5.0 for k in range(int(t_end / 2 / 5), int(t_end / 5))]
    mean_out = sum(held(stored, t) for t in times) / len(times)
    print(f"time-average of the output: {mean_out:.4f} (source: {45 + 5 / 300:.4f})")
    assert abs(mean_out - (45 + 5 / 300)) < 0.02
