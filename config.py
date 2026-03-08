from __future__ import annotations

import math
import logging

from dataclasses import dataclass

from homeassistant.core import HomeAssistant

from .const import (
    CONF_DEADBAND,
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
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

MAX_PATTERN_ENTITIES = 100


# ------------------------------------------------------------
# Small numeric helper (NO behavior change)
# ------------------------------------------------------------
def _float_or_default(value, default):
    try:
        return float(value)
    except Exception:
        return default


@dataclass
class LowpassCfg:
    source: str
    tau: float
    name: str | None
    rounding: int | None

    circular: float | None

    deadband: float | None
    deadband_k_sigma: float
    deadband_tau_sigma: float
    min_rate_dt: float
    max_rate_dt: float

    prefix: str
    suffix: str

    unique_id: str | None
    debug: bool


@dataclass(frozen=True, slots=True)
class CfgMeta:
    name_final: str
    slug: str
    use_name: bool
    unique_id: str
    unique_id_seed: str
    is_pattern: bool


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

    raw_circular = item.get(CONF_CIRCULAR)
    if raw_circular is None:
        circular = None

    elif isinstance(raw_circular, str):
        v = raw_circular.strip().lower()

        if v == "2pi":
            circular = 2 * math.pi
        else:
            circular = _float_or_default(raw_circular, None)

    else:
        circular = _float_or_default(raw_circular, None)

    if circular is not None and circular <= 0:
        _LOGGER.warning("Invalid circular=%r, must be > 0, disabling circular mode", raw_circular)
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

    return LowpassCfg(
        source=source,
        tau=tau,
        name=name,
        circular=circular,
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
    )


# ------------------------------------------------------------
# Name + slug computation (single source of truth)
# ------------------------------------------------------------
def compute_name_and_slug(
    hass: HomeAssistant,
    cfg: LowpassCfg,
    is_pattern: bool,
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

        name_final = f"{base} {cfg.suffix}"

    slug = name_final.lower()
    slug = "".join(c if c.isalnum() else "_" for c in slug)
    while "__" in slug:
        slug = slug.replace("__", "_")
    slug = slug.strip("_")

    return name_final, slug, use_name


# ------------------------------------------------------------
# Precompute meta (single source of truth)
# ------------------------------------------------------------
def make_meta(
    hass: HomeAssistant,
    cfg: LowpassCfg,
    *,
    is_pattern: bool,
) -> CfgMeta:

    name_final, slug, use_name = compute_name_and_slug(
        hass, cfg, is_pattern
    )

    # NEW UNIQUE_ID RULE:
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

    unique_id = f"{DOMAIN}::{seed}"

    return CfgMeta(
        name_final=name_final,
        slug=slug,
        use_name=use_name,
        unique_id=unique_id,
        unique_id_seed=seed,
        is_pattern=is_pattern,
    )