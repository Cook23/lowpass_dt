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


@pytest.mark.parametrize(
    "key, default",
    [
        ("tau", 60.0),
        ("deadband_k_sigma", 2.0),
        ("deadband_tau_sigma", 6000.0),
        ("max_rate_dt", 10.0),
        ("min_rate_dt", 3600.0),
    ],
)
@pytest.mark.parametrize("raw", [-1, "abc", True, math.nan, math.inf, "nan"])
def test_invalid_number_falls_back_with_a_warning(caplog, key, default, raw):
    """Text, a boolean, nan, inf or a value out of range: the default, and a warning (text
    and nan used to be taken quietly, nan and inf kept as they were)."""
    assert getattr(cfg(**{key: raw}), key) == default
    assert f"Invalid {key}=" in caplog.text


@pytest.mark.parametrize("key", ["tau", "deadband_k_sigma", "deadband_tau_sigma"])
def test_zero_is_invalid_where_it_must_be_positive(caplog, key):
    cfg(**{key: 0})
    assert f"Invalid {key}=" in caplog.text


def test_absent_option_is_quiet(caplog):
    cfg()
    assert caplog.text == ""


@pytest.mark.parametrize("raw", [-1, "abc", True, math.nan, math.inf])
def test_invalid_deadband_is_adaptive_with_a_warning(caplog, raw):
    assert cfg(deadband=raw).deadband is None
    assert "Invalid deadband=" in caplog.text


@pytest.mark.parametrize("raw, expected", [(2, 2), (2.0, 2), ("3", 3), (0, 0), (-1, -1)])
def test_round_values(caplog, raw, expected):
    assert cfg(round=raw).rounding == expected
    assert caplog.text == ""


@pytest.mark.parametrize("raw", [2.7, True, "abc", math.nan])
def test_invalid_round_is_dynamic_with_a_warning(caplog, raw):
    assert cfg(round=raw).rounding is None
    assert "Invalid round=" in caplog.text


def test_unique_id_ignored_for_patterns_with_a_warning(caplog):
    c = build_cfg({"unique_id": "x"}, source="sensor.x", allow_unique_id=False)
    assert c.unique_id is None
    assert "Ignoring unique_id=" in caplog.text


def test_rates_must_be_ordered():
    c = cfg(max_rate_dt=100, min_rate_dt=50)
    assert (c.max_rate_dt, c.min_rate_dt) == (10.0, 3600.0)


def test_one_rate_alone_moves_the_other_default():
    """min_rate_dt: 2 alone asks for a write every 2 s at least (discussion #6)."""
    c = cfg(min_rate_dt=2)
    assert (c.max_rate_dt, c.min_rate_dt) == (1.0, 2.0)
    c = cfg(max_rate_dt=7200)
    assert (c.max_rate_dt, c.min_rate_dt) == (7200.0, 14400.0)


def test_deadband_zero_is_valid():
    assert cfg(deadband=0).deadband == 0.0


@pytest.mark.parametrize("raw, expected", [("Last", "last"), ("none", "none"), ("ZERO", "zero"), (0, "0"), ("unknown", "unknown"), ("bad", None)])
def test_silence_values(raw, expected):
    assert cfg(silence=raw).silence == expected


def test_name_ignored_for_patterns():
    c = build_cfg({"name": "X"}, source="sensor.x", allow_unique_id=False)
    assert c.name is None


def test_debug_must_be_bool():
    assert cfg(debug="yes").debug is False
    assert cfg(debug=True).debug is True


async def test_config_schema_passes_options_through(hass):
    """CONFIG_SCHEMA only checks that lowpass_dt: is a mapping (or empty): options are
    checked by build_cfg, which warns and falls back to defaults."""
    from custom_components.lowpass_dt import CONFIG_SCHEMA

    conf = {"lowpass_dt": {"sensors": [{"source": "sensor.x", "tau": "abc", "tua": 1}]}, "other": {}}
    assert CONFIG_SCHEMA(conf) == conf
    assert cfg(tau="abc").tau == 60.0       # (then build_cfg: the default, with a warning)
    assert CONFIG_SCHEMA({"lowpass_dt": None}) == {"lowpass_dt": None}
    with pytest.raises(Exception):
        CONFIG_SCHEMA({"lowpass_dt": ["not", "a", "mapping"]})
