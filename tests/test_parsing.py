"""Source state parsing and output rounding helpers."""
import pytest

from custom_components.lowpass_dt.publisher import _update_decimals
from custom_components.lowpass_dt.sensor import LowpassDtSensor

parse = LowpassDtSensor._parse_source_value


@pytest.mark.parametrize(
    "state, expected",
    [
        ("12.5", (12.5, None)),
        ("-3", (-3.0, None)),
        ("1e3", (1000.0, None)),
        ("12.5 W", (12.5, "W")),
        ("unavailable", (None, None)),
        ("unknown", (None, None)),
        ("nan", (None, None)),
        ("inf", (None, None)),
        ("on", (None, None)),
    ],
)
def test_parse_ok(state, expected):
    assert parse(None, state) == expected


@pytest.mark.parametrize(
    "state, wrong",
    [
        ("12,5", (12.0, ",5")),                                    # decimal comma
        ("2026-10-10", (2026.0, "-10-10")),                        # a date
        ("2026-10-10T07:00:00+00:00", (2026.0, "-10-10T07:00:00+00:00")),  # a timestamp
        ("1.2.3", (1.2, ".3")),                                    # a version number
    ],
)
def test_parse_accepts_non_numbers(state, wrong):
    """Known weakness: the 'number + unit' regex turns these into a value and a fake unit."""
    assert parse(None, state) == wrong


@pytest.mark.parametrize("deadband, decimals", [(0.5, 2), (0.05, 3), (5, 1), (0.0001, 5)])
def test_initial_decimals_from_deadband(deadband, decimals):
    assert _update_decimals(deadband, None) == decimals


def _steady(deadband):
    d = _update_decimals(deadband, None)
    for _ in range(10):
        d = _update_decimals(deadband, d)
    return d


@pytest.mark.parametrize("deadband", [0.5, 0.05, 0.005])
def test_first_decimals_differ_from_steady_state(deadband):
    """Known inconsistency: the first publish uses one decimal more than every following one
    with the same deadband (the initial formula and the hysteresis band disagree)."""
    assert _update_decimals(deadband, None) == _steady(deadband) + 1


def test_decimals_hysteresis_keeps_value_inside_band():
    d = _steady(0.05)
    assert _update_decimals(0.06, d) == d
