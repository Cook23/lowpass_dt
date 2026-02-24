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
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

MAX_PATTERN_ENTITIES = 100


# ------------------------------------------------------------
# Utility: derive rounding precision from fixed deadband
# ------------------------------------------------------------
def _default_round_from_deadband(deadband: float | None) -> int:
    """Derive display rounding from fixed deadband."""
    if deadband is not None and deadband >= 10:
        return 0
    if deadband is not None and deadband >= 1:
        return 1
    if deadband is None or deadband <= 0:
        return 2
    return max(0, int(math.ceil(-math.log10(deadband))) + 1)


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

    deadband: float | None
    deadband_k_sigma: float
    deadband_tau_sigma: float
    min_rate_dt: float
    max_rate_dt: float

    prefix: str
    suffix: str

    # Only allowed for sensors/source mode (explicit), ignored/disabled for patterns/match
    unique_id: str | None


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
        _LOGGER.warning("tau must be > 0, got %r, using default 60.0", tau)
        tau = 60.0

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
        _LOGGER.warning("deadband_k_sigma must be > 0, using default 2.0")
        deadband_k_sigma = 2.0

    # deadband_tau_sigma (must be > 0)
    default_deadband_tau_sigma = max(100.0 * tau, 10.0)
    raw_tau_sigma = item.get(CONF_DEADBAND_TAU_SIGMA)

    if raw_tau_sigma is None:
        deadband_tau_sigma = default_deadband_tau_sigma
    else:
        deadband_tau_sigma = _float_or_default(raw_tau_sigma, default_deadband_tau_sigma)

    if deadband_tau_sigma <= 0:
        _LOGGER.warning(
            "deadband_tau_sigma must be > 0, using derived default"
        )
        deadband_tau_sigma = default_deadband_tau_sigma

    # rounding
    if CONF_ROUND in item:
        try:
            rounding = int(item.get(CONF_ROUND))
        except Exception:
            _LOGGER.warning("Invalid rounding=%r, using fallback 2", item.get(CONF_ROUND))
            rounding = 2
    else:
        rounding = _default_round_from_deadband(deadband)

    # min_rate_dt (must be >= 0)
    min_rate_dt = _float_or_default(item.get(CONF_MIN_RATE_DT, 3600.0), 3600.0)
    if min_rate_dt < 0:
        _LOGGER.warning("min_rate_dt must be >= 0, using default 3600.0")
        min_rate_dt = 3600.0

    # max_rate_dt (must be >= 0)
    max_rate_dt = _float_or_default(item.get(CONF_MAX_RATE_DT, 10.0), 10.0)
    if max_rate_dt < 0:
        _LOGGER.warning("max_rate_dt must be >= 0, using default 10.0")
        max_rate_dt = 10.0

    # prefix
    prefix = item.get(CONF_PREFIX, "lp_")
    if not isinstance(prefix, str):
        _LOGGER.warning("prefix must be a string, using default 'lp_'")
        prefix = "lp_"

    # suffix
    suffix = item.get(CONF_SUFFIX, "(Filtered)")
    if not isinstance(suffix, str):
        _LOGGER.warning("suffix must be a string, using default '(Filtered)'")
        suffix = "(Filtered)"

    # unique_id (sensors/source only)
    unique_id: str | None = None
    if allow_unique_id and (CONF_UNIQUE_ID in item):
        raw_uid = item.get(CONF_UNIQUE_ID)
        if isinstance(raw_uid, str) and raw_uid.strip():
            unique_id = raw_uid.strip()
        else:
            _LOGGER.warning(
                "unique_id must be a non-empty string. Ignored for source=%s.",
                source,
            )

    return LowpassCfg(
        source=source,
        tau=tau,
        name=item.get(CONF_NAME),
        rounding=rounding,
        deadband=deadband,
        deadband_k_sigma=deadband_k_sigma,
        deadband_tau_sigma=deadband_tau_sigma,
        min_rate_dt=min_rate_dt,
        max_rate_dt=max_rate_dt,
        prefix=prefix,
        suffix=suffix,
        unique_id=unique_id,
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

    use_name = False
    if cfg.name and not is_pattern:
        use_name = True
    elif cfg.name and is_pattern:
        _LOGGER.warning(
            "Lowpass: 'name' ignored for pattern-based sensor %s. Use explicit sensor config.",
            cfg.source,
        )

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
                _LOGGER.warning(
                    "Lowpass: invalid source entity_id '%s' (missing domain).",
                    cfg.source,
                )
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