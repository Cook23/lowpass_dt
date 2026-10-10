"""build_cfg: parsing and validation of the YAML options (pure, no Home Assistant)."""
import math

import pytest

from custom_components.lowpass_dt.config import build_cfg


def cfg(**item):
    return build_cfg(item, source="sensor.x", allow_unique_id=True)


def test_defaults():
    c = cfg()
    assert c.tau == 60.0
    assert c.circular is None and c.circular_auto is True
    assert c.deadband is None
    assert c.deadband_k_sigma == 2.0
    assert c.deadband_tau_sigma == 6000.0  # max(100 * tau, 10)
    assert c.max_rate_dt == 10.0 and c.min_rate_dt == 3600.0
    assert c.prefix == "lp_" and c.suffix == "(Filtered)"
    assert c.rounding is None and c.silence is None and c.debug is False


@pytest.mark.parametrize(
    "raw, period, auto",
    [
        (None, None, True),
        ("none", None, True),
        (" None ", None, True),
        (False, None, False),
        (True, None, False),          # invalid: warning, off
        (360, 360.0, False),
        ("360", 360.0, False),
        (6.28, 6.28, False),
        ("2pi", 2 * math.pi, False),
        ("2 PI", 2 * math.pi, False),
        (0, None, False),             # period <= 0: off
        (-5, None, False),
        ("abc", None, False),
        ("", None, False),
        ("inf", None, False),
        ("nan", None, False),
    ],
)
def test_circular_values(raw, period, auto):
    c = cfg(circular=raw) if raw is not None else cfg()
    assert c.circular_auto is auto
    if period is None:
        assert c.circular is None
    else:
        assert c.circular == pytest.approx(period)


@pytest.mark.parametrize("tau", [0, -1, "abc"])
def test_invalid_tau_falls_back(tau):
    assert cfg(tau=tau).tau == 60.0


def test_rates_must_be_ordered():
    c = cfg(max_rate_dt=100, min_rate_dt=50)
    assert (c.max_rate_dt, c.min_rate_dt) == (10.0, 3600.0)


def test_negative_deadband_disabled():
    assert cfg(deadband=-1).deadband is None
    assert cfg(deadband=0).deadband == 0.0


@pytest.mark.parametrize("raw, expected", [("Last", "last"), ("ZERO", "zero"), (0, "0"), ("unknown", "unknown"), ("bad", None)])
def test_silence_values(raw, expected):
    assert cfg(silence=raw).silence == expected


def test_name_ignored_for_patterns():
    c = build_cfg({"name": "X"}, source="sensor.x", allow_unique_id=False)
    assert c.name is None


def test_debug_must_be_bool():
    assert cfg(debug="yes").debug is False
    assert cfg(debug=True).debug is True
