"""The filtered entity inside a real Home Assistant core, with a controlled clock."""
import math
import threading
from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)

from custom_components.lowpass_dt.injector import TauInjector

from .conftest import setup_lowpass

SRC = "sensor.src"
OUT = "sensor.lp_src"
ATTRS = {"unit_of_measurement": "W", "state_class": "measurement", "friendly_name": "Src"}


class Clock:
    """Moves the frozen clock and fires the timers that became due."""

    def __init__(self, hass, freezer):
        self.hass, self.freezer = hass, freezer
        self.t0 = dt_util.utcnow()

    async def at(self, seconds):
        now = self.t0 + timedelta(seconds=seconds)
        self.freezer.move_to(now)
        async_fire_time_changed(self.hass, now)
        await self.hass.async_block_till_done()

    async def run_until(self, start, end, step=1.0):
        t = start
        while t <= end:
            await self.at(t)
            t += step


@pytest.fixture
def clock(hass, freezer):
    return Clock(hass, freezer)


@pytest.fixture
def registered(hass):
    """The filtered entity as it is after the first restart: already in the entity
    registry under its final entity_id (see test_new_entity_loses_silence_detection)."""
    er.async_get(hass).async_get_or_create("sensor", "lowpass_dt", "lowpass_dt::lp_src", suggested_object_id="lp_src")


def tick(t):
    """A real sensor never reports exactly on time: a perfectly regular clock (σ = 0) would
    make each value a few hundredths of a second late a silence."""
    return t + (0.2 if t % 2 else -0.2)


def jitter(t):
    """A real sensor never repeats exactly: Home Assistant drops a state equal to the
    previous one (no state_changed event), which lowpass_dt would take for a silence."""
    return 0.001 if int(t) % 2 else -0.001


async def feed(hass, clock, samples, attrs=ATTRS):
    """samples: list of (t, value). Returns the output state after each sample."""
    out = []
    for t, x in samples:
        await clock.at(t)
        hass.states.async_set(SRC, str(x), attrs)
        await hass.async_block_till_done()
        out.append(hass.states.get(OUT).state)
    return out


def recorded(hass, entity_id):
    """Collect every state written for entity_id (what the Recorder would store)."""
    states = []

    def _listener(event):
        if event.data["entity_id"] == entity_id and event.data["new_state"] is not None:
            states.append(event.data["new_state"].state)

    hass.bus.async_listen("state_changed", _listener)
    return states


# ------------------------------------------------------------------ creation


async def test_entity_created_with_source_attributes(hass: HomeAssistant, clock):
    hass.states.async_set(SRC, "10", ATTRS)
    await setup_lowpass(hass, sensors=[{"source": SRC, "tau": 10}])
    st = hass.states.get(OUT)
    assert st is not None
    assert st.state == "10.0"
    assert st.attributes["unit_of_measurement"] == "W"
    assert st.attributes["state_class"] == "measurement"
    assert st.attributes["friendly_name"] == "Src (Filtered)"
    assert st.attributes["source"] == SRC


async def test_name_option_sets_entity_id(hass: HomeAssistant, clock):
    hass.states.async_set(SRC, "10", ATTRS)
    await setup_lowpass(hass, sensors=[{"source": SRC, "name": "Power smooth"}])
    assert hass.states.get("sensor.power_smooth") is not None


async def test_unknown_key_is_reported(hass: HomeAssistant, caplog):
    hass.states.async_set(SRC, "10", ATTRS)
    await setup_lowpass(hass, sensors=[{"source": SRC, "tua": 10}])
    assert "unknown key 'tua'" in caplog.text


async def test_debug_attributes(hass: HomeAssistant, clock):
    hass.states.async_set(SRC, "10", ATTRS)
    await setup_lowpass(hass, sensors=[{"source": SRC, "tau": 10, "debug": True, "max_rate_dt": 1, "deadband": 0.01}])
    await feed(hass, clock, [(10, 12), (20, 14)])
    attrs = hass.states.get(OUT).attributes
    assert attrs["source"] == SRC
    assert attrs["tau_filter"] == 10
    assert set(attrs["deadband"]) >= {"deadband", "deadband_filtered_mean", "deadband_filtered_sigma"}
    assert set(attrs["dt_output"]) == {"dt_output", "dt_output_mean", "dt_output_sigma"}


async def test_friendly_name_follows_a_renamed_source(hass: HomeAssistant, clock):
    """Renaming the source (its friendly name) renames the filtered entity at once, not only
    after a restart; the source's values alone never cause a write."""
    hass.states.async_set(SRC, "10", ATTRS)
    await setup_lowpass(hass, sensors=[{"source": SRC, "tau": 60, "deadband": 5}])
    writes = recorded(hass, OUT)
    await feed(hass, clock, [(t, 10 + jitter(t)) for t in range(10, 60, 10)])
    assert writes == []                                    # values under the deadband
    await feed(hass, clock, [(70, 10.001)], {**ATTRS, "friendly_name": "Kitchen"})
    assert hass.states.get(OUT).attributes["friendly_name"] == "Kitchen (Filtered)"
    await feed(hass, clock, [(80, 10.002)], {**ATTRS, "friendly_name": "Kitchen oven"})
    assert hass.states.get(OUT).attributes["friendly_name"] == "Kitchen oven (Filtered)"
    n = len(writes)
    await feed(hass, clock, [(t, 10 + jitter(t)) for t in range(90, 140, 10)], {**ATTRS, "friendly_name": "Kitchen oven"})
    assert len(writes) == n                                # same name: no write


# ------------------------------------------------------------------ filtering and publishing


async def test_filter_follows_a_step(hass: HomeAssistant, clock):
    hass.states.async_set(SRC, "0", ATTRS)
    await setup_lowpass(hass, sensors=[{"source": SRC, "tau": 30, "deadband": 0.01, "max_rate_dt": 1}])
    await feed(hass, clock, [(t, 0 if t == 0 else 100 + jitter(t // 5)) for t in range(0, 300, 5)])
    y = float(hass.states.get(OUT).state)
    assert 95 < y <= 100


async def test_fixed_deadband_suppresses_small_changes(hass: HomeAssistant, clock):
    hass.states.async_set(SRC, "20", ATTRS)
    await setup_lowpass(hass, sensors=[{"source": SRC, "tau": 60, "deadband": 1.0, "max_rate_dt": 0.5}])
    # (warm-up: a new entity learns the source's rhythm from its first intervals, and may
    # take one of the first late values for a silence)
    await feed(hass, clock, [(tick(t), 20 + (0.2 if t % 2 else -0.2)) for t in range(1, 20)])
    writes = recorded(hass, OUT)
    await feed(hass, clock, [(tick(t), 20 + (0.2 if t % 2 else -0.2)) for t in range(20, 80)])
    assert writes == []       # noise of ±0.2 under a 1.0 deadband: nothing written


async def test_integral_term_publishes_noise_under_the_deadband(hass: HomeAssistant, clock):
    """err_i = err × (time since the last write) / tau: the current error held over the whole
    time since the last write, not an accumulated integral. Far outside the normal regime
    (tau = 1 s, a value every second), noise ten times under the deadband is then written
    about every 8 s. In the normal regime (tau ~ 10 source periods) the difference with an
    accumulated integral is a few % of writes and accuracy (measured with
    test_behavior.py's scenarios), so the term is kept as is."""
    hass.states.async_set(SRC, "20", ATTRS)
    await setup_lowpass(hass, sensors=[{"source": SRC, "tau": 1, "deadband": 1.0, "max_rate_dt": 0.5}])
    writes = recorded(hass, OUT)
    await feed(hass, clock, [(t, 20 + (0.2 if t % 2 else -0.2)) for t in range(1, 60)])
    assert len(writes) >= 5


async def test_max_rate_dt_limits_writes(hass: HomeAssistant, clock):
    hass.states.async_set(SRC, "0", ATTRS)
    await setup_lowpass(hass, sensors=[{"source": SRC, "tau": 60, "deadband": 0.01, "max_rate_dt": 10}])
    await feed(hass, clock, [(tick(t), t) for t in range(1, 20)])   # (warm-up, as above)
    writes = recorded(hass, OUT)
    await feed(hass, clock, [(tick(t), t) for t in range(20, 80)])  # a ramp, one value per second
    assert 4 <= len(writes) <= 7                              # about one write per 10 s


# ------------------------------------------------------------------ silence


async def test_silence_converges_to_last_value(hass: HomeAssistant, clock, registered):
    hass.states.async_set(SRC, "0", ATTRS)
    await setup_lowpass(hass, sensors=[{"source": SRC, "tau": 60, "deadband": 0.01, "max_rate_dt": 1}])
    await feed(hass, clock, [(t, (0 if t < 100 else 50) + jitter(t // 10)) for t in range(0, 110, 10)])
    mid = float(hass.states.get(OUT).state)
    assert mid < 40                                   # still converging when the source stops
    await clock.run_until(111, 1200, step=5)          # the source is silent for ~18 min
    assert float(hass.states.get(OUT).state) == pytest.approx(50.0, abs=0.002)  # driven to the last value


async def test_injection_runs_in_the_event_loop(hass: HomeAssistant, clock, registered, monkeypatch):
    """The injector's timer callbacks are @callback: they run in the event loop, never in
    a worker thread where they would change the filter state while the loop uses it."""
    threads = set()
    orig = TauInjector._inject_once

    def spy(self):
        threads.add(threading.current_thread() is threading.main_thread())
        return orig(self)

    monkeypatch.setattr(TauInjector, "_inject_once", spy)
    hass.states.async_set(SRC, "0", ATTRS)
    await setup_lowpass(hass, sensors=[{"source": SRC, "tau": 60, "deadband": 0.01}])
    await feed(hass, clock, [(t, 10 + jitter(t // 10)) for t in range(10, 110, 10)])
    await clock.run_until(111, 400, step=5)
    assert threads == {True}


@pytest.mark.parametrize("mode, expected", [("zero", "0.0"), ("unknown", "unknown")])
async def test_silence_modes(hass: HomeAssistant, clock, registered, mode, expected):
    hass.states.async_set(SRC, "0", ATTRS)
    await setup_lowpass(hass, sensors=[{"source": SRC, "tau": 30, "deadband": 0.01, "max_rate_dt": 1, "silence": mode}])
    await feed(hass, clock, [(t, 20 + jitter(t // 10)) for t in range(10, 110, 10)])
    await clock.run_until(111, 1500, step=5)
    assert hass.states.get(OUT).state == expected


async def test_end_of_silence_marker(hass: HomeAssistant, clock, registered):
    hass.states.async_set(SRC, "0", ATTRS)
    await setup_lowpass(hass, sensors=[{"source": SRC, "tau": 30, "deadband": 0.01, "max_rate_dt": 1}])
    await feed(hass, clock, [(t, 20 + jitter(t // 10)) for t in range(10, 110, 10)])
    await clock.run_until(111, 900, step=5)
    assert float(hass.states.get(OUT).state) == pytest.approx(20.0, abs=0.002)
    writes = recorded(hass, OUT)
    await feed(hass, clock, [(905, 80)])
    # the plateau is closed by a marker at the last value + 10^-(decimals+1), so that the
    # Recorder stores it; with ZOH the output only moves at the next sample
    assert len(writes) == 1
    assert float(writes[0]) == pytest.approx(20.0, abs=0.01)
    assert float(writes[0]) != float(hass.states.get(SRC).state)
    await feed(hass, clock, [(915, 80.001)])
    assert len(writes) == 2 and float(writes[1]) > 25


async def test_new_entity_has_silence_detection(hass: HomeAssistant, clock):
    """A new entity is registered directly under its final entity_id (no rename, which
    used to remove and re-add it and stop its injector until the next restart)."""
    hass.states.async_set(SRC, "0", ATTRS)
    await setup_lowpass(hass, sensors=[{"source": SRC, "tau": 30, "deadband": 0.01, "max_rate_dt": 1}])
    assert er.async_get(hass).async_get(OUT) is not None
    assert er.async_get(hass).async_get("sensor.src_filtered") is None
    await feed(hass, clock, [(t, 20 + jitter(t // 10)) for t in range(10, 110, 10)])
    await clock.run_until(111, 900, step=5)
    assert float(hass.states.get(OUT).state) == pytest.approx(20.0, abs=0.002)


async def test_renaming_entity_id_in_ui_is_kept(hass: HomeAssistant, clock, registered):
    """A user renaming the filtered entity in the UI keeps the new id, and its silence
    detection keeps working (the injector starts again when the entity is re-added)."""
    hass.states.async_set(SRC, "0", ATTRS)
    await setup_lowpass(hass, sensors=[{"source": SRC, "tau": 30, "deadband": 0.01, "max_rate_dt": 1}])
    er.async_get(hass).async_update_entity(OUT, new_entity_id="sensor.my_power")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.my_power") is not None
    assert hass.states.get(OUT) is None
    ent = hass.data["entity_components"]["sensor"].get_entity("sensor.my_power")
    assert ent.injector._stopped is False
    for t in range(10, 110, 10):
        await clock.at(t)
        hass.states.async_set(SRC, str(20 + jitter(t // 10)), ATTRS)
        await hass.async_block_till_done()
    await clock.run_until(111, 900, step=5)
    assert float(hass.states.get("sensor.my_power").state) == pytest.approx(20.0, abs=0.002)


# ------------------------------------------------------------------ adaptive deadband


def sigma_time_constant(ent, now):
    """The time constant of the EMA of σ, as filter.py computes it."""
    tau_s_min = 10.0 * ent.cfg.tau
    if ent.core.t_sigma_start is None:
        return tau_s_min
    return min(ent.cfg.deadband_tau_sigma, max(tau_s_min, now - ent.core.t_sigma_start))


async def test_sigma_time_constant_grows_on_a_new_entity(hass: HomeAssistant, clock, registered):
    """The EMA of σ starts at 10 tau and grows with time up to deadband_tau_sigma. It stayed
    at 10 tau on every entity created since March 2026: t_sigma_start was only set
    when the filter had no value yet, and the sensor gives it one before any sample."""
    hass.states.async_set(SRC, "10", ATTRS)
    await setup_lowpass(hass, sensors=[{"source": SRC, "tau": 20, "deadband_tau_sigma": 1000}])
    ent = hass.data["entity_components"]["sensor"].get_entity(OUT)
    await feed(hass, clock, [(t, 10 + jitter(t)) for t in range(2, 1200, 2)])
    assert ent.core.t_sigma_start is not None
    now = dt_util.utcnow().timestamp()
    assert sigma_time_constant(ent, now) == 1000


async def test_sigma_start_restored_empty_is_set_again(hass: HomeAssistant, clock):
    """An entity saved with t_sigma_start empty (created since March 2026) gets it at its
    first sample after the upgrade."""
    t = dt_util.utcnow().timestamp()
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(OUT, "10.0", {"unit_of_measurement": "W", "state_class": "measurement"}),
                {
                    "low_pass": {"y": 10.0, "t_prev": t - 5, "x_prev": 10.0, "time_last_pub": t - 5, "circular": None},
                    "ema_source": {"src_mean": 10.0, "src_var": 0.0, "t_sigma_start": None},
                    "ema_dt_source": {"dt_mean": 2.0, "dt_m2": 4.0, "t_last_source": t - 5},
                    "ema_dt_output": {"dt_output_mean": 10.0, "dt_output_m2": 100.0, "decimals": 2},
                },
            )
        ],
    )
    hass.states.async_set(SRC, "10", ATTRS)
    await setup_lowpass(hass, sensors=[{"source": SRC, "tau": 20}])
    ent = hass.data["entity_components"]["sensor"].get_entity(OUT)
    await feed(hass, clock, [(2, 10.001)])
    assert ent.core.t_sigma_start is not None


async def test_total_increasing_reset_while_stopped(hass: HomeAssistant, clock):
    """A daily meter reset while Home Assistant was stopped: the output follows the new
    count (it stayed at the old total, up to 1.4.1, until the meter passed it again)."""
    t = dt_util.utcnow().timestamp()
    attrs = {"unit_of_measurement": "kWh", "state_class": "total_increasing"}
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(OUT, "1000.0", attrs),
                {
                    "low_pass": {"y": 1000.0, "t_prev": t - 600, "x_prev": 1000.0, "time_last_pub": t - 600, "circular": None},
                    "ema_source": {"src_mean": 1000.0, "src_var": 0.0, "t_sigma_start": t - 7200},
                    "ema_dt_source": {"dt_mean": 10.0, "dt_m2": 100.0, "t_last_source": t - 600},
                    "ema_dt_output": {"dt_output_mean": 60.0, "dt_output_m2": 3600.0, "decimals": 1},
                },
            )
        ],
    )
    hass.states.async_set(SRC, "2", attrs)
    await setup_lowpass(hass, sensors=[{"source": SRC, "tau": 60}])
    await feed(hass, clock, [(10 * k, 2 + 0.1 * k) for k in range(1, 30)], attrs=attrs)
    assert float(hass.states.get(OUT).state) < 10


# ------------------------------------------------------------------ circular


async def test_angle_detected_from_degree_unit(hass: HomeAssistant, clock):
    attrs = {"unit_of_measurement": "°", "state_class": "measurement"}
    hass.states.async_set(SRC, "350", attrs)
    await setup_lowpass(hass, sensors=[{"source": SRC, "tau": 100, "deadband": 0.01, "max_rate_dt": 1}])
    await feed(hass, clock, [(10 * k, 10 if k % 2 else 350) for k in range(1, 200)], attrs)
    y = float(hass.states.get(OUT).state)
    assert min(y, 360 - y) < 20
    assert 0 <= y < 360


async def test_temperature_unit_is_not_an_angle(hass: HomeAssistant, clock):
    attrs = {"unit_of_measurement": "°C", "state_class": "measurement"}
    hass.states.async_set(SRC, "350", attrs)
    await setup_lowpass(hass, sensors=[{"source": SRC, "tau": 100, "deadband": 0.01, "max_rate_dt": 1}])
    await feed(hass, clock, [(10 * k, 10 if k % 2 else 350) for k in range(1, 200)], attrs)
    assert 140 < float(hass.states.get(OUT).state) < 220


# ------------------------------------------------------------------ total_increasing


async def test_total_increasing_never_decreases(hass: HomeAssistant, clock):
    attrs = {"unit_of_measurement": "kWh", "state_class": "total_increasing"}
    hass.states.async_set(SRC, "100", attrs)
    await setup_lowpass(hass, sensors=[{"source": SRC, "tau": 10, "deadband": 0.01, "max_rate_dt": 1}])
    outs = await feed(hass, clock, [(10, 110), (20, 120), (30, 119), (40, 121), (50, 125)], attrs)
    vals = [float(v) for v in outs]
    assert vals == sorted(vals)


async def test_total_increasing_reset_is_accepted(hass: HomeAssistant, clock):
    attrs = {"unit_of_measurement": "kWh", "state_class": "total_increasing"}
    hass.states.async_set(SRC, "1000", attrs)
    await setup_lowpass(hass, sensors=[{"source": SRC, "tau": 10, "deadband": 0.01, "max_rate_dt": 1}])
    await feed(hass, clock, [(10, 1010), (20, 1020), (30, 3), (40, 5)], attrs)
    assert float(hass.states.get(OUT).state) < 10


# ------------------------------------------------------------------ restart


async def test_restore_keeps_full_precision_filter_state(hass: HomeAssistant, clock):
    t = dt_util.utcnow().timestamp()
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(OUT, "12.3", {"unit_of_measurement": "W", "state_class": "measurement"}),
                {
                    "low_pass": {"y": 12.3456789, "t_prev": t - 5, "x_prev": 12.0, "time_last_pub": t - 5, "err_i": 0.0, "circular": None},
                    "ema_source": {"src_mean": 12.3, "src_var": 0.01, "t_sigma_start": t - 10000},
                    "ema_dt_source": {"dt_mean": 10.0, "dt_m2": 101.0, "t_last_source": t - 5},
                    "ema_dt_output": {"dt_output_mean": 10.0, "dt_output_m2": 100.0, "decimals": 2},
                },
            )
        ],
    )
    hass.states.async_set(SRC, "12", ATTRS)
    await setup_lowpass(hass, sensors=[{"source": SRC, "tau": 60}])
    ent = hass.data["entity_components"]["sensor"].get_entity(OUT)
    assert ent.core.src_mean is not None
    assert ent.injector.dt_mean == 10.0
    assert hass.states.get(OUT).state == "12.3"
    # downtime (5 s) weighted on the value before shutdown (12.0), not the startup value
    assert ent.core.y == pytest.approx(12.3456789 + (1 - math.exp(-5 / 60)) * (12.0 - 12.3456789), abs=1e-6)


async def test_new_entity_interval_statistics_start_from_the_first_interval(hass: HomeAssistant, clock):
    """A new entity starts its silence statistics from the source's first interval — they
    used to start from tau (an hour here), which held the silence limit at tau for about
    ninety samples."""
    hass.states.async_set(SRC, "10", ATTRS)
    await setup_lowpass(hass, sensors=[{"source": SRC, "tau": 3600}])
    for k in range(1, 5):
        await clock.at(5 * k)
        hass.states.async_set(SRC, str(10 + k + jitter(k)), ATTRS)
        await hass.async_block_till_done()
    inj = hass.data["entity_components"]["sensor"].get_entity(OUT).injector
    assert inj.dt_mean == pytest.approx(5.0, abs=0.1)
    assert inj.limit < 10
