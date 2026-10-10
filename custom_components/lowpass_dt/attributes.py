"""An attribute of an entity as the source of a filter.

The value and its unit are read as history-explorer-card reads them (its
src/history-series.js): the unit the value gives ("12.5 °C"), else the one Home Assistant
shows the attribute in (its frontend's tables). The device_class and state_class, which the
card does not need, are derived from that unit, the source's domain and, last, the
attribute's name.
"""
from __future__ import annotations

import math
import re

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.components.sensor.const import (
    DEVICE_CLASS_STATE_CLASSES,
    DEVICE_CLASS_UNITS,
)

# ------------------------------------------------------------
# Value: a number, or a number followed by a unit (12.5 °C, 12,5 %)
# ------------------------------------------------------------
# (the card's regex: the unit must not start with a digit or a separator — not a time,
# a date or a range)
_NUMBER_UNIT = re.compile(r"^\s*([-+]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?)\s*([^\d\s:.,/+-].*?)\s*$")


def attribute_label(attribute: str) -> str:
    """An attribute as people read it: current_temperature → Current temperature."""
    s = attribute.replace("_", " ")
    return s[:1].upper() + s[1:]


def _parse_value(raw) -> tuple[float | None, str | None]:
    if isinstance(raw, bool):
        return None, None
    if isinstance(raw, (int, float)):
        value = float(raw)
        return (value, None) if math.isfinite(value) else (None, None)
    if not isinstance(raw, str):
        return None, None
    try:
        value = float(raw)
        return (value, None) if math.isfinite(value) else (None, None)
    except ValueError:
        pass
    m = _NUMBER_UNIT.match(raw)
    if m:
        value = float(m.group(1).replace(",", "."))
        if math.isfinite(value):
            return value, m.group(2)
    return None, None


# ------------------------------------------------------------
# Units Home Assistant shows attributes in (same tables as the card)
# ------------------------------------------------------------
# Weather: the X of the X_unit attribute giving it, or the unit itself
_WEATHER_UNITS = {
    "temperature": "temperature", "apparent_temperature": "temperature", "dew_point": "temperature", "templow": "temperature",
    "pressure": "pressure", "wind_speed": "wind_speed", "wind_gust_speed": "wind_speed",
    "visibility": "visibility", "precipitation": "precipitation",
    "humidity": {"unit": "%"}, "cloud_coverage": {"unit": "%"}, "precipitation_probability": {"unit": "%"},
    # (not in the card's table: Home Assistant gives it in degrees, and without a unit the
    # filter would not know it is an angle)
    "wind_bearing": {"unit": "°"},
}

# By domain: the unit, or the unit and the factor turning the value into it
_DOMAIN_UNITS = {
    "climate": {"humidity": "%", "current_humidity": "%", "target_humidity_low": "%", "target_humidity_high": "%",
                "target_humidity_step": "%", "min_humidity": "%", "max_humidity": "%"},
    "cover": {"current_position": "%", "current_tilt_position": "%"},
    "fan": {"percentage": "%"},
    "humidifier": {"humidity": "%", "current_humidity": "%", "min_humidity": "%", "max_humidity": "%", "target_humidity_step": "%"},
    "light": {"color_temp": "mired", "max_mireds": "mired", "min_mireds": "mired", "color_temp_kelvin": "K",
              "min_color_temp_kelvin": "K", "max_color_temp_kelvin": "K", "brightness": {"unit": "%", "factor": 100 / 255}},
    "sun": {"azimuth": "°", "elevation": "°"},
    "valve": {"current_position": "%"},
    "sensor": {"battery_level": "%"},
    "media_player": {"volume_level": {"unit": "%", "factor": 100}},
}

# Shown in the temperature unit of Home Assistant, outside a weather entity
_TEMPERATURE_ATTRIBUTES = (
    "temperature", "current_temperature", "target_temperature", "target_temp_temp", "target_temp_high",
    "target_temp_low", "target_temp_step", "min_temp", "max_temp",
)


def _domain_unit(domain: str, attribute: str) -> tuple[str, float] | None:
    u = _DOMAIN_UNITS.get(domain, {}).get(attribute)
    if u is None:
        return None
    return (u, 1.0) if isinstance(u, str) else (u["unit"], u["factor"])


def _units_of(attrs: dict) -> dict[str, str]:
    """An attribute X_unit is the unit of the other attributes whose name contains X."""
    return {
        k[:-5]: u for k, u in attrs.items()
        if k.endswith("_unit") and len(k) > 5 and isinstance(u, str) and u
    }


def _attribute_unit(temperature_unit: str | None, domain: str, attribute: str, attrs: dict) -> str | None:
    if attribute.endswith("_unit"):
        return None
    units = _units_of(attrs)
    if domain == "weather" and attribute in _WEATHER_UNITS:
        w = _WEATHER_UNITS[attribute]
        return w["unit"] if isinstance(w, dict) else units.get(w)
    if domain != "weather":
        d = _domain_unit(domain, attribute)
        if d:
            return d[0]
        if attribute in _TEMPERATURE_ATTRIBUTES and temperature_unit:
            return temperature_unit
    matches = sorted((x for x in units if x in attribute), key=len, reverse=True)
    return units[matches[0]] if matches else None


def attribute_value(source: str, attribute: str, attrs) -> tuple[float | None, str | None]:
    """The numeric value of attribute in the source's attributes, in the unit Home Assistant
    shows it in (a light's brightness in %), and the unit the value itself gives."""
    if not attrs or attribute not in attrs:
        return None, None
    value, unit = _parse_value(attrs[attribute])
    if value is None:
        return None, None
    d = _domain_unit(source.split(".", 1)[0], attribute)
    if d and d[1] != 1.0:
        value *= d[1]
    return value, unit


# ------------------------------------------------------------
# device_class and state_class
# ------------------------------------------------------------
def _device_class(value: str) -> SensorDeviceClass | None:
    try:
        return SensorDeviceClass(value)
    except ValueError:  # (not in this Home Assistant version: wind_direction before 2025.x)
        return None


def _allows(dc: SensorDeviceClass | None, unit: str | None) -> bool:
    return dc is not None and unit in DEVICE_CLASS_UNITS.get(dc, ())


# Domains whose attributes are settings or positions, not physical quantities
_NO_DEVICE_CLASS_DOMAINS = ("light", "sun", "cover", "valve", "fan", "media_player")

# By domain, before anything else
_DOMAIN_DEVICE_CLASSES = {
    "weather": {"pressure": "atmospheric_pressure", "wind_bearing": "wind_direction"},
}

# By name (last resort, or to choose between the device classes a unit allows), in order
_NAME_DEVICE_CLASSES = (
    (r"dew_?point|temp(erature)?(_|$)|(^|_)temp", ("temperature",)),
    (r"humidity", ("humidity",)),
    (r"moisture", ("moisture",)),
    (r"pressure", ("pressure", "atmospheric_pressure")),
    (r"wind_?(gust|speed)|gust", ("wind_speed",)),
    (r"speed", ("speed",)),
    (r"bearing|wind_?dir", ("wind_direction",)),
    (r"battery", ("battery",)),
    (r"power_factor", ("power_factor",)),
    (r"power", ("power", "apparent_power", "reactive_power")),
    (r"energy", ("energy",)),
    (r"voltage", ("voltage",)),
    (r"(^|_)current$", ("current",)),
    (r"frequency", ("frequency",)),
    (r"illuminance|(^|_)lux", ("illuminance",)),
    (r"visibility|distance", ("distance",)),
    (r"precipitation_intensity|rain_rate", ("precipitation_intensity",)),
    (r"precipitation|rain", ("precipitation",)),
    (r"co2|carbon_dioxide", ("carbon_dioxide",)),
    (r"(^|_)co$|carbon_monoxide", ("carbon_monoxide",)),
    (r"pm_?2_?5", ("pm25",)),
    (r"pm_?10", ("pm10",)),
    (r"pm_?1($|_)", ("pm1",)),
    (r"ozone", ("ozone",)),
    (r"no2|nitrogen_dioxide", ("nitrogen_dioxide",)),
    (r"aqi|air_quality_index", ("aqi",)),
    (r"signal|rssi", ("signal_strength",)),
    (r"weight|mass", ("weight",)),
    (r"volume", ("volume",)),
)

# Units that alone say too little: an angle in ° is not always a wind direction, a K not
# always a temperature (a light's color_temp_kelvin)
_AMBIGUOUS_UNITS = ("°", "K", "%")

# Between several device classes a unit allows, and nothing else to choose: the general one
_GENERIC_DEVICE_CLASSES = ("temperature", "pressure", "speed", "distance", "energy", "volume", "weight", "data_size", "duration")


def derive_device_class(domain: str, attribute: str, unit: str | None) -> str | None:
    """The device_class of a filtered attribute: one its unit allows, chosen by the source's
    domain, else by the unit alone when it allows a single one, else by the attribute's name."""
    if domain in _NO_DEVICE_CLASS_DOMAINS:
        return None
    hint = _DOMAIN_DEVICE_CLASSES.get(domain, {}).get(attribute)
    if hint is not None and _allows(_device_class(hint), unit):
        return hint

    if unit is not None:
        candidates = [str(dc) for dc, units in DEVICE_CLASS_UNITS.items() if unit in units]
        if len(candidates) == 1 and unit not in _AMBIGUOUS_UNITS:
            return candidates[0]
    else:
        candidates = []

    name = attribute.lower()
    for pattern, classes in _NAME_DEVICE_CLASSES:
        if re.search(pattern, name):
            for c in classes:
                if _allows(_device_class(c), unit):
                    return c

    if unit not in _AMBIGUOUS_UNITS:
        for c in _GENERIC_DEVICE_CLASSES:
            if c in candidates:
                return c
    return None


def derive_state_class(device_class: str | None) -> str:
    """measurement, unless the device_class does not allow it (an angle, an energy)."""
    dc = _device_class(device_class) if device_class else None
    allowed = {str(s) for s in DEVICE_CLASS_STATE_CLASSES.get(dc, ())} if dc else set()
    if not allowed or SensorStateClass.MEASUREMENT in allowed:
        return "measurement"
    for s in ("measurement_angle", "total"):
        if s in allowed:
            return s
    return sorted(allowed)[0]


# Attributes in ° that are angles around a circle (an elevation or a tilt is not)
_ANGLE_NAMES = re.compile(r"bearing|direction|azimuth|heading|(^|_)yaw|wind_?dir")


def is_circular_attribute(attribute: str) -> bool:
    return bool(_ANGLE_NAMES.search(attribute.lower()))


# ------------------------------------------------------------
# What the filtered entity takes from the source, for an attribute
# ------------------------------------------------------------
def derived_attributes(temperature_unit: str | None, source: str, attribute: str, attrs) -> dict:
    """unit_of_measurement, device_class and state_class of a filtered attribute — never
    the source entity's own (a weather entity's state is its condition)."""
    attrs = attrs or {}
    domain = source.split(".", 1)[0]
    value, value_unit = attribute_value(source, attribute, attrs)
    unit = value_unit or _attribute_unit(temperature_unit, domain, attribute, attrs)
    device_class = derive_device_class(domain, attribute, unit)
    out = {"device_class": device_class, "state_class": derive_state_class(device_class)}
    if unit:
        out["unit_of_measurement"] = unit
    return out
