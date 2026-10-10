"""LowpassCore: the filter math (pure, no Home Assistant)."""
import math

import pytest

from custom_components.lowpass_dt.config import build_cfg
from custom_components.lowpass_dt.filter import LowpassCore


def core(**item):
    return LowpassCore(build_cfg(item, source="sensor.x"))


def run(c, samples):
    """samples: list of (t, x). Returns y after each sample."""
    out = []
    for t, x in samples:
        c.update_from_source(x, t)
        out.append(c.y)
    return out


def test_first_sample_initialises_output():
    c = core(tau=10)
    assert c.update_from_source(5.0, 100.0) == 0.0
    assert c.y == 5.0


def test_zoh_new_value_only_counts_from_next_interval():
    c = core(tau=10)
    run(c, [(0, 0.0), (10, 100.0)])
    # dt=10 was spent at the previous value 0: no move yet
    assert c.y == 0.0
    run(c, [(20, 100.0)])
    assert c.y == pytest.approx(100.0 * (1 - math.exp(-10 / 10)))  # alpha = 1 - exp(-dt / tau)


def test_constant_input_is_a_fixed_point():
    c = core(tau=10)
    ys = run(c, [(t, 42.0) for t in range(0, 100, 7)])
    assert all(y == 42.0 for y in ys)


def test_step_response_does_not_depend_on_sampling_rate():
    """The same step, observed at the same time, whether the source reports every 5 s or
    once: the exact first order 1 - exp(-t / tau) in both cases."""
    tau = 60.0

    fast = core(tau=tau)
    run(fast, [(0, 0.0), (1e-9, 1.0)] + [(5.0 * k, 1.0) for k in range(1, 61)])   # every 5 s, t = 300

    slow = core(tau=tau)
    run(slow, [(0, 0.0), (1e-9, 1.0), (300.0, 1.0)])                           # one value at t = 300

    exact = 1 - math.exp(-300 / tau)
    assert fast.y == pytest.approx(exact, abs=1e-6)
    assert slow.y == pytest.approx(exact, abs=1e-6)


def test_injected_steps_do_not_change_the_output():
    """Computing the output more often (injection during a silence) gives the same value."""
    a, b = core(tau=30), core(tau=30)
    run(a, [(0, 0.0), (10, 50.0), (200, 50.0)])
    run(b, [(0, 0.0), (10, 50.0)])
    for t in range(20, 200, 7):
        b.update_synthetic(50.0, float(t))
    b.update_from_source(50.0, 200.0)
    assert b.y == pytest.approx(a.y, rel=1e-12)


def test_circular_mean_crosses_north():
    c = core(tau=200, circular=360)
    run(c, [(0, 350.0)] + [(10 * k, 10.0 if k % 2 else 350.0) for k in range(1, 400)])
    # oscillating 350 / 10: the output stays near 0 (or 360), never near 180
    d = min(c.y, 360 - c.y)
    assert d < 15
    assert 0 <= c.y < 360


def test_without_circular_the_same_signal_averages_to_180():
    c = core(tau=200, circular=False)
    run(c, [(0, 350.0)] + [(10 * k, 10.0 if k % 2 else 350.0) for k in range(1, 400)])
    assert 160 < c.y < 200


def test_adaptive_deadband_follows_sigma():
    c = core(tau=1, deadband_k_sigma=2)
    run(c, [(t, 10.0 + (1 if t % 2 else -1)) for t in range(0, 2000)])
    assert c.src_sigma > 0
    assert c.effective_deadband() == pytest.approx(2 * c.src_sigma)


def test_fixed_deadband_wins():
    c = core(deadband=0.5)
    assert c.effective_deadband() == 0.5


def test_synthetic_update_pulls_towards_last_value():
    c = core(tau=10)
    run(c, [(0, 0.0), (10, 100.0)])
    c.update_synthetic(100.0, 20.0)
    assert c.y == pytest.approx(100.0 * (1 - math.exp(-1)))
