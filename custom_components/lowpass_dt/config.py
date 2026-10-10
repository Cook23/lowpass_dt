from __future__ import annotations

import math
import logging
import re

from dataclasses import dataclass

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ATTRIBUTE,
    CONF_DEADBAND,
    CONF_DEVICE_CLASS,
    CONF_STATE_CLASS,
    CONF_UNIT,
    CONF_DEADBAND_K_SIGMA,
    CONF_DEADBAND_TAU_SIGMA,
    CONF_MAX_RATE_DT,
    CONF_MIN_RATE_DT,
    CONF_NAME,
    CONF_PREFIX,
    CONF_ROUND,
    CONF_SUFFIX,
    CONF_TAU,
    CONF_UNIQUE_ID,
    CONF_DEBUG,
    CONF_CIRCULAR,
    CONF_SILENCE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

MAX_PATTERN_ENTITIES = 100


# ------------------------------------------------------------
# Small numeric helper
# ------------------------------------------------------------
def _float_or_default(value, default):
    try:
        return float(value)
    except Exception:
        return default


# ------------------------------------------------------------
# Source state parsing: a number, optionally followed by a unit
# ------------------------------------------------------------
# The unit must not start with a digit or a separator: a date ("2026-10-10"), a time
# ("10:30"), a version ("1.2.3") or a decimal comma ("12,5") is not a measurement.
_NUMBER_UNIT = re.compile(r"^\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*([^\d\s.,:;/+\-]\S*)?\s*$")


def parse_state(state_str) -> tuple[float | None, str | None]:
    """Numeric value and optional unit of a source state, or (None, None)."""
    try:
        value = float(state_str)
        return (value, None) if math.isfinite(value) else (None, None)
    except (TypeError, ValueError):
        pass
    m = _NUMBER_UNIT.match(state_str) if isinstance(state_str, str) else None
    if m:
        value = float(m.group(1))
        if math.isfinite(value):
            return value, m.group(2)
    return None, None


@dataclass
class LowpassCfg:
    source: str
    tau: float
    name: str | None
    rounding: int | None

    circular: float | None
    # circular absent / null / 'none': resolved from the source once its unit and
    # state_class are known (see LowpassSensor._resolve_auto_circular)
    circular_auto: bool
    silence: str | None

    deadband: float | None
    deadband_k_sigma: float
    deadband_tau_sigma: float
    min_rate_dt: float
    max_rate_dt: float

    prefix: str
    suffix: str

    unique_id: str | None
    debug: bool

    # filter this attribute of the source instead of its state
    attribute: str | None = None

    # overrides of what the source gives or what is derived ('none': none at all)
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = None

    @property
    def key(self) -> str:
        """What the filter reads: the source, or the source and one of its attributes."""
        return cfg_key(self.source, self.attribute)


def cfg_key(source: str, attribute: str | None) -> str:
    return f"{source}.{attribute}" if attribute else source


@dataclass(frozen=True, slots=True)
class CfgMeta:
    name_final: str
    slug: str
    use_name: bool
    unique_id: str
    unique_id_seed: str


# ------------------------------------------------------------
# device_class / state_class override: a value Home Assistant knows, or 'none'
# ------------------------------------------------------------
def _class_override(item: dict, key: str, enum, source: str) -> str | None:
    raw = item.get(key)
    if raw is None:
        return None
    value = str(raw).strip().lower()
    if value == "none" or value in {str(v) for v in enum}:
        return value
    _LOGGER.warning("Invalid %s=%r for source=%r, ignored (expected one of Home Assistant's, or none)", key, raw, source)
    return None


# ------------------------------------------------------------
# Build LowpassCfg from raw config entry
# ------------------------------------------------------------
def build_cfg(item: dict, *, source: str, allow_unique_id: bool = False) -> LowpassCfg:
    """Parse config dict into LowpassCfg."""

    # tau (must be > 0)
    tau = _float_or_default(item.get(CONF_TAU, 60.0), 60.0)
    if tau <= 0:
        _LOGGER.warning("Invalid tau=%r, must be > 0, using default 60.0", tau)
        tau = 60.0

    # circular: absent / null / 'none' = automatic (state_class measurement_angle or a
    # unit of exactly '°' gives 360, resolved at runtime), false = never, a number or a
    # numeric string = the period, '2pi' = 2π. Anything else, or a period <= 0, disables
    # it with a warning. (Same values as the history-explorer-card `circular` option.)
    raw_circular = item.get(CONF_CIRCULAR)
    circular = None
    circular_auto = False
    if raw_circular is None or ( isinstance(raw_circular, str) and raw_circular.strip().lower() == "none" ):
        circular_auto = True

    elif raw_circular is False:
        pass

    elif isinstance(raw_circular, bool):
        _LOGGER.warning("Invalid circular=%r, expected false, none, a period > 0 or '2pi', disabling circular mode", raw_circular)

    else:
        if isinstance(raw_circular, str):
            v = "".join(raw_circular.split()).lower()
            circular = 2 * math.pi if v == "2pi" else _float_or_default(v, None)
        else:
            circular = _float_or_default(raw_circular, None)

        if circular is None or not math.isfinite(circular) or circular <= 0:
            _LOGGER.warning("Invalid circular=%r, expected false, none, a period > 0 or '2pi', disabling circular mode", raw_circular)
            circular = None

    # deadband (None allowed, but if provided must be >= 0)
    deadband = item.get(CONF_DEADBAND)
    if deadband is not None:
        deadband = _float_or_default(deadband, None)
        if deadband is None or deadband < 0:
            _LOGGER.warning("Invalid deadband=%r, disabling deadband", item.get(CONF_DEADBAND))
            deadband = None

    # deadband_k_sigma (must be > 0)
    deadband_k_sigma = _float_or_default(item.get(CONF_DEADBAND_K_SIGMA, 2.0), 2.0)
    if deadband_k_sigma <= 0:
        _LOGGER.warning("Invalid deadband_k_sigma=%r, must be > 0, using default 2.0", item.get(CONF_DEADBAND_K_SIGMA))
        deadband_k_sigma = 2.0

    # deadband_tau_sigma (must be > 10)
    default_deadband_tau_sigma = max(100.0 * tau, 10.0)
    raw_tau_sigma = item.get(CONF_DEADBAND_TAU_SIGMA)

    if raw_tau_sigma is None:
        deadband_tau_sigma = default_deadband_tau_sigma
    else:
        deadband_tau_sigma = _float_or_default(raw_tau_sigma, default_deadband_tau_sigma)

    if deadband_tau_sigma <= 0:
        _LOGGER.warning("Invalid deadband_tau_sigma=%r, must be > 0, using derived default", item.get(CONF_DEADBAND_TAU_SIGMA))
        deadband_tau_sigma = default_deadband_tau_sigma

    # rounding
    if CONF_ROUND in item:
        try:
            rounding = int(item.get(CONF_ROUND))
        except Exception:
            _LOGGER.warning("Invalid rounding=%r, using dynamic rounding", item.get(CONF_ROUND))
            rounding = None
    else:
        rounding = None

    # max_rate_dt (must be >= 0)
    max_rate_dt = _float_or_default(item.get(CONF_MAX_RATE_DT, 10.0), 10.0)
    # min_rate_dt (must be >= 0)
    min_rate_dt = _float_or_default(item.get(CONF_MIN_RATE_DT, 3600.0), 3600.0)

    if max_rate_dt < 0:
        _LOGGER.warning("Invalid max_rate_dt=%r, must be >= 0, using default 10.0", item.get(CONF_MAX_RATE_DT))
        max_rate_dt = 10.0

    if min_rate_dt < 0:
        _LOGGER.warning("Invalid min_rate_dt=%r, must be >= 0, using default 3600.0", item.get(CONF_MIN_RATE_DT))
        min_rate_dt = 3600.0

    # Only one of the two given, at odds with the other's default: the other follows it
    # (min_rate_dt: 2 alone asks for a write every 2 s at least, not for the defaults)
    if max_rate_dt >= min_rate_dt:
        if CONF_MAX_RATE_DT not in item and CONF_MIN_RATE_DT in item:
            max_rate_dt = min_rate_dt / 2
        elif CONF_MIN_RATE_DT not in item and CONF_MAX_RATE_DT in item:
            min_rate_dt = max_rate_dt * 2

    if max_rate_dt >= min_rate_dt:
        _LOGGER.warning("Invalid max_rate_dt=%r >= min_rate_dt=%r, publish interval must satisfy max_rate_dt < min_rate_dt, using defaults 10.0 and 3600.0", item.get(CONF_MAX_RATE_DT), item.get(CONF_MIN_RATE_DT))
        max_rate_dt = 10.0
        min_rate_dt = 3600.0

    # prefix
    prefix = item.get(CONF_PREFIX, "lp_")
    if not isinstance(prefix, str):
        _LOGGER.warning("Invalid prefix=%r, must be a string, using default 'lp_'", item.get(CONF_PREFIX))
        prefix = "lp_"

    # suffix
    suffix = item.get(CONF_SUFFIX, "(Filtered)")
    if not isinstance(suffix, str):
        _LOGGER.warning("Invalid suffix=%r, must be a string, using default '(Filtered)'", item.get(CONF_SUFFIX))
        suffix = "(Filtered)"

    # unique_id (sensors/source only)
    unique_id: str | None = None
    if allow_unique_id and (CONF_UNIQUE_ID in item):
        raw_uid = item.get(CONF_UNIQUE_ID)
        if isinstance(raw_uid, str) and raw_uid.strip():
            unique_id = raw_uid.strip()
        else:
            _LOGGER.warning("Invalid unique_id=%r, must be a non-empty string, ignored for source=%r.", item.get(CONF_UNIQUE_ID), source)

    # incompatibilities name vs prefix/suffix
    name=item.get(CONF_NAME)
    if allow_unique_id and name:
        if CONF_PREFIX in item:
            _LOGGER.warning("Ignoring prefix=%r because name=%r is defined for sensor %r",item.get(CONF_PREFIX), name, source)
        if CONF_SUFFIX in item:
            _LOGGER.warning("Ignoring suffix=%r because name=%r is defined for sensor %r",item.get(CONF_SUFFIX), name, source)

    elif name and not allow_unique_id:
        _LOGGER.warning("Ignoring name=%r for pattern-based sensor %r, use explicit sensor config", name, source)
        name = None

    # debug mode
    raw_debug = item.get(CONF_DEBUG, False)
    if isinstance(raw_debug, bool):
        debug = raw_debug
    else:
        _LOGGER.warning("Invalid debug=%r, must be true/false, using default False", raw_debug)
        debug = False

    # silence mode
    raw_silence = item.get(CONF_SILENCE)
    if raw_silence is None:
        silence = None
    else:
        raw_silence = str(raw_silence).strip().lower()
        if raw_silence in ("last", "none", "0", "zero", "unknown"):
            silence = raw_silence
        else:
            _LOGGER.warning("Invalid silence=%r, must be None/Last/Zero/Unknown, using default None", item.get(CONF_SILENCE))
            silence = None

    # attribute (a non-empty string), or written after the entity_id as in
    # history-explorer-card: climate.salon.current_temperature (an entity_id has one dot)
    attribute = item.get(CONF_ATTRIBUTE)
    if attribute is not None:
        if isinstance(attribute, str) and attribute.strip():
            attribute = attribute.strip()
        else:
            _LOGGER.warning("Invalid attribute=%r, must be a non-empty string, filtering the state of %r", attribute, source)
            attribute = None
    parts = source.split(".", 2)
    if len(parts) == 3:
        if attribute is not None and attribute != parts[2]:
            _LOGGER.warning("source=%r names attribute %r, but attribute=%r: using %r", source, parts[2], attribute, attribute)
        source = f"{parts[0]}.{parts[1]}"
        attribute = attribute or parts[2]

    # unit (a non-empty string)
    unit = item.get(CONF_UNIT)
    if unit is not None:
        if isinstance(unit, str) and unit.strip():
            unit = unit.strip()
        else:
            _LOGGER.warning("Invalid unit=%r, must be a non-empty string, ignored for source=%r", unit, source)
            unit = None

    device_class = _class_override(item, CONF_DEVICE_CLASS, SensorDeviceClass, source)
    state_class = _class_override(item, CONF_STATE_CLASS, SensorStateClass, source)

    return LowpassCfg(
        source=source,
        attribute=attribute,
        unit=unit,
        device_class=device_class,
        state_class=state_class,
        tau=tau,
        name=name,
        circular=circular,
        circular_auto=circular_auto,
        rounding=rounding,
        deadband=deadband,
        deadband_k_sigma=deadband_k_sigma,
        deadband_tau_sigma=deadband_tau_sigma,
        min_rate_dt=min_rate_dt,
        max_rate_dt=max_rate_dt,
        prefix=prefix,
        suffix=suffix,
        unique_id=unique_id,
        debug=debug,
        silence=silence,
    )


# ------------------------------------------------------------
# Friendly name from the source's: "Salon (Filtered)", "Salon : Current temperature (Filtered)"
# ------------------------------------------------------------
def source_display_name(base: str, cfg: LowpassCfg) -> str:
    if cfg.attribute:
        from .attributes import attribute_label
        base = f"{base} : {attribute_label(cfg.attribute)}"
    return f"{base} {cfg.suffix}"


def _slugify(text: str) -> str:
    slug = "".join(c if c.isalnum() else "_" for c in text.lower())
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_")


# ------------------------------------------------------------
# Name + slug computation (single source of truth)
# ------------------------------------------------------------
def compute_name_and_slug(
    hass: HomeAssistant,
    cfg: LowpassCfg,
) -> tuple[str, str, bool]:
    """Compute final friendly_name, slug(name_final), and name_mode flag."""

    use_name = cfg.name is not None
    if use_name:
        name_final = cfg.name
    else:
        st = hass.states.get(cfg.source)
        base = None
        if st is not None:
            base = (st.attributes or {}).get("friendly_name")
        if base and base.startswith("sensor."):
            base = None
        if not base:
            if "." in cfg.source:
                base = cfg.source.split(".", 1)[1]
            else:
                _LOGGER.warning("Invalid source entity_id=%r (missing domain)", cfg.source)
                base = cfg.source

        name_final = source_display_name(base, cfg)

    slug = _slugify(name_final)

    return name_final, slug, use_name


# ------------------------------------------------------------
# Precompute meta (single source of truth)
# ------------------------------------------------------------
def make_meta(
    hass: HomeAssistant,
    cfg: LowpassCfg,
) -> CfgMeta:

    name_final, slug, use_name = compute_name_and_slug(hass, cfg)

    # unique_id:
    # - sensors/source: if cfg.unique_id is provided, use it as seed
    # - otherwise: seed = prefix + source_object_id (without "sensor.")
    if cfg.unique_id:
        seed = cfg.unique_id
    else:
        source_object_id = (
            cfg.source.split(".", 1)[1]
            if "." in cfg.source
            else cfg.source
        )
        seed = f"{cfg.prefix}{source_object_id}"
        if cfg.attribute:
            # (the whole entity_id: weather.home and sensor.home, or sensor.home_temperature,
            # never share a unique_id)
            seed = f"{cfg.prefix}{cfg.source}.{cfg.attribute}"

    unique_id = f"{DOMAIN}::{seed}"

    return CfgMeta(
        name_final=name_final,
        slug=slug,
        use_name=use_name,
        unique_id=unique_id,
        unique_id_seed=seed,
    )