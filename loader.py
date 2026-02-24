from __future__ import annotations

import fnmatch
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback, CoreState
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_SENSORS,
    CONF_SOURCE,
    CONF_PATTERNS,
    CONF_MATCH,
    DOMAIN,
)

from .config import (
    MAX_PATTERN_ENTITIES,
    LowpassCfg,
    CfgMeta,
    build_cfg,
    make_meta,
)

_LOGGER = logging.getLogger(__name__)


# ------------------------------------------------------------
# Helpers (pure refactor — no behavior change)
# ------------------------------------------------------------

def _validate_pattern_item(p: dict) -> str | None:
    if not isinstance(p, dict):
        _LOGGER.warning(
            "LP patterns[]: invalid item type %s (expected dict). Skipped.",
            type(p).__name__,
        )
        return None

    pat = p.get(CONF_MATCH)
    if not isinstance(pat, str) or not pat:
        _LOGGER.warning(
            "LP patterns[]: missing/invalid 'match' (expected non-empty string). Skipped."
        )
        return None

    return pat


def _validate_sensor_item(item: dict) -> str | None:
    if not isinstance(item, dict):
        _LOGGER.warning(
            "LP sensors[]: invalid item type %s (expected dict). Skipped.",
            type(item).__name__,
        )
        return None

    source = item.get(CONF_SOURCE)
    if not isinstance(source, str) or not source:
        _LOGGER.warning(
            "LP sensors[]: missing/invalid 'source' (expected non-empty string). Skipped."
        )
        return None

    if "." not in source:
        _LOGGER.warning(
            "LP sensors[]: invalid source '%s' (missing domain like 'sensor.xxx'). Skipped.",
            source,
        )
        return None

    return source


# ------------------------------------------------------------
# Setup entry
# ------------------------------------------------------------
async def async_setup_entry_loader(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
    *,
    sensor_cls,
) -> None:

    data = entry.data or {}

    sensors_list = data.get(CONF_SENSORS, []) or []
    patterns_list = data.get(CONF_PATTERNS, []) or []

    # ------------------------------------------------------------
    # Track existing lowpass entities (recursion guard)
    # ------------------------------------------------------------
    reg0 = er.async_get(hass)
    own_entity_ids: set[str] = {
        e.entity_id for e in reg0.entities.values() if e.platform == DOMAIN
    }

    # ------------------------------------------------------------
    # Build explicit configs
    # ------------------------------------------------------------
    explicit: dict[str, LowpassCfg] = {}

    for item in sensors_list:
        source = _validate_sensor_item(item)
        if not source:
            continue

        # sensors/source chaining allowed
        explicit[source] = build_cfg(
            item,
            source=source,
            allow_unique_id=True,
        )

    # ------------------------------------------------------------
    # Initial pattern matching
    # ------------------------------------------------------------
    matched_cfgs: dict[str, LowpassCfg] = {}

    all_sensor_entities = [
        st.entity_id for st in hass.states.async_all("sensor")
    ]

    for p in patterns_list:
        pat = _validate_pattern_item(p)
        if not pat:
            continue

        local_count = 0

        for eid in all_sensor_entities:

            if eid in explicit:
                continue

            if not fnmatch.fnmatch(eid, pat):
                continue

            # Recursion protection
            if eid in own_entity_ids:
                _LOGGER.warning(
                    "LP patterns[]: match '%s' includes '%s' (lowpass_dt sensor). Skipped to avoid recursion.",
                    pat,
                    eid,
                )
                continue

            local_count += 1

            if local_count > MAX_PATTERN_ENTITIES:
                _LOGGER.warning(
                    "LP patterns[]: pattern '%s' exceeded limit (%d). Aborted for this pattern.",
                    pat,
                    MAX_PATTERN_ENTITIES,
                )
                break

            matched_cfgs[eid] = build_cfg(
                p,
                source=eid,
                allow_unique_id=False,
            )

    # ------------------------------------------------------------
    # Precompute meta
    # ------------------------------------------------------------
    cfgs = list(explicit.values()) + list(matched_cfgs.values())

    cfg_meta: dict[str, CfgMeta] = {}
    desired_unique_ids: set[str] = set()

    for cfg in cfgs:
        is_pattern = cfg.source in matched_cfgs
        meta = make_meta(hass, cfg, is_pattern=is_pattern)
        desired_unique_ids.add(meta.unique_id)
        cfg_meta[cfg.source] = meta

    # ------------------------------------------------------------
    # Cleanup (pattern mode only)
    # ------------------------------------------------------------
    @callback
    def _cleanup_late(_event):
        reg2 = er.async_get(hass)
        removed = 0

        for entity in list(reg2.entities.values()):
            if entity.config_entry_id != entry.entry_id:
                continue
            if entity.platform != DOMAIN:
                continue
            if entity.unique_id not in desired_unique_ids:
                reg2.async_remove(entity.entity_id)
                removed += 1

        _LOGGER.debug("LP cleanup late: removed=%d", removed)

    if patterns_list:
        unsub_cleanup = hass.bus.async_listen_once(
            "homeassistant_started",
            _cleanup_late,
        )
        entry.async_on_unload(unsub_cleanup)

    # ------------------------------------------------------------
    # Create entities
    # ------------------------------------------------------------
    entities = [
        sensor_cls(
            hass,
            cfg,
            is_pattern=cfg_meta[cfg.source].is_pattern,
            precomputed=cfg_meta[cfg.source],
        )
        for cfg in cfgs
    ]

    async_add_entities(entities)

    # Update recursion guard
    for ent in entities:
        own_entity_ids.add(ent.entity_id)
        suggested = getattr(ent, "_attr_suggested_object_id", None)
        if suggested:
            own_entity_ids.add(f"sensor.{suggested}")

    # ------------------------------------------------------------
    # Dynamic pattern matching
    # ------------------------------------------------------------
    pattern_dynamic_created: set[str] = set()

    @callback
    def _maybe_add_new_entity(event):

        if hass.state != CoreState.running:
            return

        new_eid = event.data.get("entity_id")
        if not new_eid:
            return

        if new_eid in explicit:
            return

        for p in patterns_list:

            pat = _validate_pattern_item(p)
            if not pat:
                continue

            if not fnmatch.fnmatch(new_eid, pat):
                continue

            st = hass.states.get(new_eid)
            if not st:
                return

            if new_eid in own_entity_ids:
                _LOGGER.warning(
                    "LP dynamic: match '%s' includes '%s' (lowpass_dt sensor). Skipped to avoid recursion.",
                    pat,
                    new_eid,
                )
                return

            cfg = build_cfg(
                p,
                source=new_eid,
                allow_unique_id=False,
            )
            meta = make_meta(hass, cfg, is_pattern=True)

            if meta.unique_id in desired_unique_ids:
                return

            if len(pattern_dynamic_created) >= MAX_PATTERN_ENTITIES:
                _LOGGER.error(
                    "Pattern dynamic creation aborted: limit %d reached.",
                    MAX_PATTERN_ENTITIES,
                )
                return

            desired_unique_ids.add(meta.unique_id)
            pattern_dynamic_created.add(meta.unique_id)

            ent = sensor_cls(
                hass,
                cfg,
                is_pattern=True,
                precomputed=meta,
            )

            async_add_entities([ent])

            own_entity_ids.add(ent.entity_id)

            suggested = getattr(ent, "_attr_suggested_object_id", None)
            if suggested:
                own_entity_ids.add(f"sensor.{suggested}")

            break

    unsub_state_changed = hass.bus.async_listen(
        "state_changed",
        _maybe_add_new_entity,
    )

    entry.async_on_unload(unsub_state_changed)