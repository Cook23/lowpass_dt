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
    "state, expected",
    [
        ("20°C", (20.0, "°C")),
        ("50 %", (50.0, "%")),
        ("-.5 kWh", (-0.5, "kWh")),
        ("1.5e2 W", (150.0, "W")),
    ],
)
def test_parse_number_with_unit(state, expected):
    assert parse(None, state) == expected


@pytest.mark.parametrize(
    "state",
    ["12,5", "2026-10-10", "2026-10-10T07:00:00+00:00", "1.2.3", "10:30", "1/2", "", "W 12"],
)
def test_parse_rejects_non_measurements(state):
    """Dates, times, versions, a decimal comma: not a number followed by a unit."""
    assert parse(None, state) == (None, None)


@pytest.mark.parametrize("deadband, decimals", [(0.5, 1), (0.05, 2), (5, 0), (0.0001, 5)])
def test_initial_decimals_from_deadband(deadband, decimals):
    assert _update_decimals(deadband, None) == decimals


def _steady(deadband):
    d = _update_decimals(deadband, None)
    for _ in range(10):
        d = _update_decimals(deadband, d)
    return d


@pytest.mark.parametrize("deadband", [5, 0.5, 0.2, 0.05, 0.03, 0.005, 0.0001])
def test_first_decimals_equal_steady_state(deadband):
    """The first publish uses the same number of decimals as the following ones."""
    assert _update_decimals(deadband, None) == _steady(deadband)


def test_decimals_hysteresis_keeps_value_inside_band():
    d = _steady(0.05)
    assert _update_decimals(0.06, d) == d
