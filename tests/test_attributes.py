"""An attribute as the source (attributes.py, and its use in sensor.py and loader.py)."""
import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.lowpass_dt.attributes import (
    attribute_label,
    attribute_value,
    derive_device_class,
    derived_attributes,
)

from .conftest import setup_lowpass

HAS_WIND_DIRECTION = "wind_direction" in {str(d) for d in SensorDeviceClass}

WEATHER = {
    "friendly_name": "Home",
    "temperature": 12.5,
    "temperature_unit": "°C",
    "pressure": 1013.2,
    "pressure_unit": "hPa",
    "humidity": 80,
    "wind_speed": 15.1,
    "wind_speed_unit": "km/h",
    "wind_bearing": 350,
    "visibility": 10,
    "visibility_unit": "km",
    "precipitation_unit": "mm",
}
CLIMATE = {"friendly_name": "Salon", "current_temperature": 20.1, "temperature": 21, "current_humidity": 45}


# ------------------------------------------------------------
# Value, unit, device_class, state_class
# ------------------------------------------------------------
def test_label():
    assert attribute_label("current_temperature") == "Current temperature"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (12, (12.0, None)),
        ("12.5", (12.5, None)),
        ("12.5 °C", (12.5, "°C")),
        ("12,5 %", (12.5, "%")),
        (True, (None, None)),
        ("sunny", (None, None)),
        ("10:30", (None, None)),
        ("2026-10-10", (None, None)),
        ([1, 2], (None, None)),
    ],
)
def test_value(raw, expected):
    assert attribute_value("sensor.x", "a", {"a": raw}) == expected


def test_value_in_the_unit_home_assistant_shows():
    assert attribute_value("light.l", "brightness", {"brightness": 255}) == (100.0, None)
    assert attribute_value("media_player.m", "volume_level", {"volume_level": 0.5}) == (50.0, None)
    assert attribute_value("sensor.x", "missing", {"a": 1}) == (None, None)


def _derived(source, attribute, attrs, temperature_unit="°C"):
    return derived_attributes(temperature_unit, source, attribute, attrs)


def test_weather():
    assert _derived("weather.home", "temperature", WEATHER) == {
        "unit_of_measurement": "°C", "device_class": "temperature", "state_class": "measurement"}
    assert _derived("weather.home", "pressure", WEATHER)["device_class"] == "atmospheric_pressure"
    assert _derived("weather.home", "pressure", WEATHER)["unit_of_measurement"] == "hPa"
    assert _derived("weather.home", "humidity", WEATHER)["device_class"] == "humidity"
    assert _derived("weather.home", "wind_speed", WEATHER) == {
        "unit_of_measurement": "km/h", "device_class": "wind_speed", "state_class": "measurement"}
    assert _derived("weather.home", "visibility", WEATHER)["device_class"] == "distance"
    bearing = _derived("weather.home", "wind_bearing", WEATHER)
    assert bearing["unit_of_measurement"] == "°"
    if HAS_WIND_DIRECTION:
        assert bearing["device_class"] == "wind_direction"
        assert bearing["state_class"] == "measurement_angle"


def test_climate_uses_the_temperature_unit_of_home_assistant():
    assert _derived("climate.salon", "current_temperature", CLIMATE, "°F") == {
        "unit_of_measurement": "°F", "device_class": "temperature", "state_class": "measurement"}
    assert _derived("climate.salon", "current_humidity", CLIMATE)["device_class"] == "humidity"


def test_domains_without_device_class():
    light = _derived("light.l", "color_temp_kelvin", {"color_temp_kelvin": 3000})
    assert light == {"unit_of_measurement": "K", "device_class": None, "state_class": "measurement"}
    assert _derived("sun.sun", "elevation", {"elevation": 12})["device_class"] is None
    assert _derived("cover.c", "current_position", {"current_position": 30})["unit_of_measurement"] == "%"


def test_x_unit_attributes_and_value_units():
    attrs = {"inside_temperature": 21, "temperature_unit": "°C", "power": "1500 W"}
    assert _derived("sensor.x", "inside_temperature", attrs)["device_class"] == "temperature"
    assert _derived("sensor.x", "power", attrs) == {
        "unit_of_measurement": "W", "device_class": "power", "state_class": "measurement"}


def test_device_class_from_the_name_or_the_unit():
    assert derive_device_class("sensor", "voltage", "V") == "voltage"
    assert derive_device_class("sensor", "anything", "W") == "power"     # the only one for W
    assert derive_device_class("sensor", "battery_level", "%") == "battery"
    assert derive_device_class("sensor", "level", "%") is None           # % alone says too little
    assert derive_device_class("sensor", "heading", "°") is None         # not a wind
    assert derive_device_class("sensor", "voltage", "W") == "power"      # the unit wins over the name
    assert derive_device_class("sensor", "co2", "ppm") == "carbon_dioxide"
    assert derive_device_class("sensor", "aqi", None) == "aqi"
    assert derive_device_class("sensor", "count", None) is None


def test_energy_is_a_total():
    d = _derived("sensor.x", "energy", {"energy": "12 kWh"})
    assert d["device_class"] == "energy" and d["state_class"] == "total"


@pytest.mark.skipif(not HAS_WIND_DIRECTION, reason="wind_direction not in this Home Assistant")
def test_wind_direction_is_an_angle():
    d = _derived("sensor.x", "wind_bearing", {"wind_bearing": "350 °"})
    assert d == {"unit_of_measurement": "°", "device_class": "wind_direction", "state_class": "measurement_angle"}


# ------------------------------------------------------------
# The filtered entity
# ------------------------------------------------------------
async def test_explicit_attribute_source(hass: HomeAssistant):
    hass.states.async_set("climate.salon", "heat", CLIMATE)
    await setup_lowpass(hass, sensors=[{"source": "climate.salon", "attribute": "current_temperature", "tau": 60}])
    st = hass.states.get("sensor.lp_salon_current_temperature")
    assert st is not None
    assert float(st.state) == pytest.approx(20.1)
    assert st.attributes["friendly_name"] == "Salon : Current temperature (Filtered)"
    assert st.attributes["unit_of_measurement"] == "°C"
    assert st.attributes["device_class"] == "temperature"
    assert st.attributes["state_class"] == "measurement"
    ent = er.async_get(hass).async_get("sensor.lp_salon_current_temperature")
    assert ent.unique_id == "lowpass_dt::lp_climate.salon.current_temperature"


async def test_only_a_new_value_of_the_attribute_is_a_sample(hass: HomeAssistant):
    hass.states.async_set("climate.salon", "heat", CLIMATE)
    await setup_lowpass(hass, sensors=[{"source": "climate.salon", "attribute": "current_temperature"}])
    ent = hass.data["entity_components"]["sensor"].get_entity("sensor.lp_salon_current_temperature")
    t0 = ent.injector.t_last_source
    # the state and another attribute change, not this one
    hass.states.async_set("climate.salon", "off", {**CLIMATE, "temperature": 18})
    await hass.async_block_till_done()
    assert ent.injector.t_last_source == t0
    hass.states.async_set("climate.salon", "off", {**CLIMATE, "current_temperature": 22.0})
    await hass.async_block_till_done()
    assert ent._last_source_value == 22.0


async def test_state_and_attributes_of_one_source(hass: HomeAssistant):
    hass.states.async_set("weather.home", "sunny", WEATHER)
    await setup_lowpass(hass, sensors=[
        {"source": "weather.home", "attribute": "temperature"},
        {"source": "weather.home", "attribute": "wind_speed"},
        {"source": "weather.home", "attribute": "condition_missing"},
        {"source": "weather.home", "attribute": "wind_bearing"},
    ])
    comp = hass.data["entity_components"]["sensor"]
    assert comp.get_entity("sensor.lp_home_wind_bearing").cfg.circular == 360.0
    assert hass.states.get("sensor.lp_home_temperature").attributes["device_class"] == "temperature"
    assert hass.states.get("sensor.lp_home_wind_speed").attributes["unit_of_measurement"] == "km/h"
    # a missing attribute: the entity exists, without a value
    assert hass.states.get("sensor.lp_home_condition_missing").state == "unknown"


async def test_name_option(hass: HomeAssistant):
    hass.states.async_set("weather.home", "sunny", WEATHER)
    await setup_lowpass(hass, sensors=[{"source": "weather.home", "attribute": "pressure", "name": "Pression"}])
    st = hass.states.get("sensor.pression")
    assert st.attributes["friendly_name"] == "Pression"
    assert st.attributes["device_class"] == "atmospheric_pressure"


async def test_sun_elevation_is_not_circular_azimuth_is(hass: HomeAssistant):
    hass.states.async_set("sun.sun", "above_horizon", {"friendly_name": "Sun", "elevation": -5, "azimuth": 350})
    await setup_lowpass(hass, sensors=[
        {"source": "sun.sun", "attribute": "elevation"},
        {"source": "sun.sun", "attribute": "azimuth"},
    ])
    comp = hass.data["entity_components"]["sensor"]
    assert comp.get_entity("sensor.lp_sun_elevation").cfg.circular is None
    assert comp.get_entity("sensor.lp_sun_azimuth").cfg.circular == 360.0
    assert float(hass.states.get("sensor.lp_sun_elevation").state) == -5


async def test_pattern_with_attribute(hass: HomeAssistant):
    hass.states.async_set("climate.salon", "heat", CLIMATE)
    hass.states.async_set("climate.cuisine", "heat", {"friendly_name": "Cuisine"})  # no such attribute
    hass.states.async_set("sensor.salon", "5", {"unit_of_measurement": "W"})
    await setup_lowpass(hass, patterns=[
        {"match": "climate.*", "attribute": "current_temperature"},
        {"match": "sensor.*"},
    ])
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.lp_salon_current_temperature") is not None
    assert hass.states.get("sensor.lp_cuisine_current_temperature") is None
    assert hass.states.get("sensor.lp_salon") is not None
    # appears later
    hass.states.async_set("climate.bureau", "heat", {"friendly_name": "Bureau", "current_temperature": 19})
    await hass.async_block_till_done()
    assert hass.states.get("sensor.lp_bureau_current_temperature") is not None
    ids = sorted(e.entity_id for e in er.async_get(hass).entities.values() if e.platform == "lowpass_dt")
    assert ids == ["sensor.lp_bureau_current_temperature", "sensor.lp_salon", "sensor.lp_salon_current_temperature"]


# ------------------------------------------------------------
# Short form and overrides
# ------------------------------------------------------------
async def test_attribute_written_after_the_entity_id(hass: HomeAssistant):
    """As in history-explorer-card: climate.salon.current_temperature."""
    hass.states.async_set("climate.salon", "heat", CLIMATE)
    await setup_lowpass(hass, sensors=[{"source": "climate.salon.current_temperature"}])
    st = hass.states.get("sensor.lp_salon_current_temperature")
    assert float(st.state) == pytest.approx(20.1)
    assert st.attributes["source"] == "climate.salon"


async def test_overrides_of_an_attribute(hass: HomeAssistant):
    hass.states.async_set("sensor.box", "ok", {"friendly_name": "Box", "level": 42, "speed": 3})
    await setup_lowpass(hass, sensors=[
        {"source": "sensor.box", "attribute": "level", "unit": "%", "device_class": "moisture"},
        {"source": "sensor.box", "attribute": "speed", "unit": "km/h"},
    ])
    level = hass.states.get("sensor.lp_box_level").attributes
    assert level["unit_of_measurement"] == "%"
    assert level["device_class"] == "moisture"
    assert level["state_class"] == "measurement"
    # (the device_class derived from the unit set)
    assert hass.states.get("sensor.lp_box_speed").attributes["device_class"] == "speed"


async def test_overrides_of_a_state(hass: HomeAssistant):
    hass.states.async_set("sensor.src", "5", {"unit_of_measurement": "W", "device_class": "power", "state_class": "measurement"})
    await setup_lowpass(hass, sensors=[{"source": "sensor.src", "unit": "kW", "device_class": "none", "state_class": "total"}])
    hass.states.async_set("sensor.src", "6", {"unit_of_measurement": "W", "device_class": "power", "state_class": "measurement"})
    await hass.async_block_till_done()
    a = hass.states.get("sensor.lp_src").attributes
    assert a["unit_of_measurement"] == "kW"
    assert "device_class" not in a
    assert a["state_class"] == "total"


def test_invalid_overrides_are_ignored(caplog):
    from custom_components.lowpass_dt.config import build_cfg

    cfg = build_cfg({"device_class": "nope", "state_class": "MEASUREMENT", "unit": ""}, source="sensor.x")
    assert cfg.device_class is None and cfg.unit is None
    assert cfg.state_class == "measurement"
    assert "Invalid device_class" in caplog.text and "Invalid unit" in caplog.text
