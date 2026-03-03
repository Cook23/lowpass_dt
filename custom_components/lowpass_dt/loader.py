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
# Helpers (pure refactor � no behavior change)
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
    # Create explicit entities immediately (boot-safe)
    # ------------------------------------------------------------

    explicit_meta: dict[str, CfgMeta] = {}
    desired_unique_ids: set[str] = set()  # unique_id reference set

    for cfg in explicit.values():
        meta = make_meta(hass, cfg, is_pattern=False)
        explicit_meta[cfg.source] = meta
        desired_unique_ids.add(meta.unique_id)

    explicit_entities = [
        sensor_cls(
            hass,
            cfg,
            is_pattern=False,
            precomputed=explicit_meta[cfg.source],
        )
        for cfg in explicit.values()
    ]

    async_add_entities(explicit_entities)

    for ent in explicit_entities:
        own_entity_ids.add(ent.entity_id)
        suggested = getattr(ent, "_attr_suggested_object_id", None)
        if suggested:
            own_entity_ids.add(f"sensor.{suggested}")

    # ------------------------------------------------------------
    # Initial pattern matching (executed after HA fully started)
    # ------------------------------------------------------------

    @callback
    def _full_rescan_after_start(_event):

        create_cfgs: dict[str, LowpassCfg] = {}   # création (filtrée)
        keep_cfgs: dict[str, LowpassCfg] = {}      # existence pure

        # Scan runtime states only (never registry)
        all_sensor_entities = [
            st.entity_id for st in hass.states.async_all("sensor")
        ]

        # ------------------------------------------------------------
        # Scan sources
        # ------------------------------------------------------------
        for p in patterns_list:

            pat = _validate_pattern_item(p)
            if not pat:
                continue

            local_count = 0

            reg = er.async_get(hass)

            for reg_entry in reg.entities.values():

                if reg_entry.domain != "sensor":
                    continue

                eid = reg_entry.entity_id

                if eid in explicit:
                    continue

                if not fnmatch.fnmatch(eid, pat):
                    continue

                if reg.async_get(eid) is None:
                    continue

                # -------- KEEP (existence only) --------
                keep_cfgs[eid] = build_cfg(
                    p,
                    source=eid,
                    allow_unique_id=False,
                )

                # -------- CREATE (filtré) --------
                st = hass.states.get(eid)
                if st is None:
                    continue

                if st.state in (None, "unavailable"):
                    continue

                # Recursion protection (entity_id only)
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
                        "LP patterns[]: pattern '%s' exceeded limit (%d).",
                        pat,
                        MAX_PATTERN_ENTITIES,
                    )
                    break

                create_cfgs[eid] = keep_cfgs[eid]

        # ------------------------------------------------------------
        # Compute keep_unique_ids
        # ------------------------------------------------------------
        keep_unique_ids: set[str] = set()

        for cfg in explicit.values():
            meta = make_meta(hass, cfg, is_pattern=False)
            keep_unique_ids.add(meta.unique_id)

        for cfg in keep_cfgs.values():
            meta = make_meta(hass, cfg, is_pattern=True)
            keep_unique_ids.add(meta.unique_id)

        # ------------------------------------------------------------
        # CLEANUP (only if source truly gone)
        # ------------------------------------------------------------
        reg = er.async_get(hass)

        for entity in list(reg.entities.values()):
            if entity.config_entry_id != entry.entry_id:
                continue
            if entity.platform != DOMAIN:
                continue

            # retrouver la source depuis unique_id
            if entity.unique_id not in keep_unique_ids:
                _LOGGER.warning(
                    "LP CLEANUP removing entity_id=%s unique_id=%s",
                    entity.entity_id,
                    entity.unique_id,
                )
                reg.async_remove(entity.entity_id)

        # ------------------------------------------------------------
        # CREATE
        # ------------------------------------------------------------
        entities = []
        reg = er.async_get(hass)

        for cfg in create_cfgs.values():

            meta = make_meta(hass, cfg, is_pattern=True)

            ent = sensor_cls(
                hass,
                cfg,
                is_pattern=True,
                precomputed=meta,
            )

            entities.append(ent)

        if entities:
            async_add_entities(entities)

            for ent in entities:
                own_entity_ids.add(ent.entity_id)
                suggested = getattr(ent, "_attr_suggested_object_id", None)
                if suggested:
                    own_entity_ids.add(f"sensor.{suggested}")

    if patterns_list:
        unsub = hass.bus.async_listen_once(
            "homeassistant_started",
            _full_rescan_after_start,
        )
        entry.async_on_unload(unsub)

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

        st = hass.states.get(new_eid)
        if not st:
            return

        if st.state in (None, "unknown", "unavailable"):
            return

        if new_eid in explicit:
            return

        for p in patterns_list:

            pat = _validate_pattern_item(p)
            if not pat:
                continue

            if not fnmatch.fnmatch(new_eid, pat):
                continue

            if new_eid in own_entity_ids:
                return

            cfg = build_cfg(
                p,
                source=new_eid,
                allow_unique_id=False,
            )

            meta = make_meta(hass, cfg, is_pattern=True)

            reg = er.async_get(hass)

            existing_entity_id = reg.async_get_entity_id(
                "sensor",
                DOMAIN,
                meta.unique_id,
            )

            if existing_entity_id is not None:
                return

            # unique_id protection (correct concept separation)
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