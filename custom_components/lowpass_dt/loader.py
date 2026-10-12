from __future__ import annotations

import fnmatch
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback, CoreState
from homeassistant.helpers import entity_registry as er

from . import const
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
    parse_state,
)

_LOGGER = logging.getLogger(__name__)


# ------------------------------------------------------------
# Helpers (pure refactor no behavior change)
# ------------------------------------------------------------

# Every known configuration key, taken from const.py (single source of truth)
_ITEM_KEYS = {v for k, v in vars(const).items() if k.startswith("CONF_")} - {CONF_SENSORS, CONF_PATTERNS}


def _warn_unknown_keys(where: str, item, allowed: set[str]) -> None:
    """Report keys that would otherwise be silently ignored (typos)."""
    if isinstance(item, dict):
        for key in item.keys() - allowed:
            _LOGGER.warning("%s: unknown key %r ignored.", where, key)


def _is_measurement(state) -> bool:
    """A state a pattern may filter: numeric, or not known yet (unknown)."""
    return state.state == "unknown" or parse_state(state.state)[0] is not None


def _validate_pattern_item(p: dict) -> str | None:
    if not isinstance(p, dict):
        _LOGGER.warning("patterns[]: invalid item type %s (expected dict). Skipped.", type(p).__name__)
        return None

    pat = p.get(CONF_MATCH)
    if not isinstance(pat, str) or not pat:
        _LOGGER.warning("patterns[]: invalid match=%r (expected non-empty string). Skipped.", pat)
        return None

    return pat


def _validate_sensor_item(item: dict) -> str | None:
    if not isinstance(item, dict):
        _LOGGER.warning("sensors[]: invalid item type %s (expected dict). Skipped.", type(item).__name__)
        return None

    source = item.get(CONF_SOURCE)
    if not isinstance(source, str) or not source:
        _LOGGER.warning("sensors[]: invalid source=%r (expected non-empty string). Skipped.", source)
        return None

    if "." not in source:
        _LOGGER.warning("sensors[]: invalid source=%r (missing domain like 'sensor.xxx'). Skipped.", source)
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

    _warn_unknown_keys(DOMAIN, data, {CONF_SENSORS, CONF_PATTERNS})
    for p in patterns_list:
        _warn_unknown_keys("patterns[]", p, _ITEM_KEYS - {CONF_SOURCE})

    # Each pattern checked once, its invalid values reported once: it is built again,
    # quietly, for each of its sources — at each change of a source, on the dynamic path
    patterns: list[tuple[dict, str]] = []
    for p in patterns_list:
        pat = _validate_pattern_item(p)
        if pat:
            build_cfg(p, source=pat, allow_unique_id=False)
            patterns.append((p, pat))

    # ------------------------------------------------------------
    # Track existing lowpass entities (recursion guard)
    # ------------------------------------------------------------
    reg0 = er.async_get(hass)
    own_entity_ids: set[str] = {
        e.entity_id for e in reg0.entities.values() if e.platform == DOMAIN
    }

    def _is_own(eid: str) -> bool:
        """A lowpass_dt entity: never filtered again (recursion guard)."""
        if eid in own_entity_ids:
            return True
        reg_entry = er.async_get(hass).async_get(eid)
        return reg_entry is not None and reg_entry.platform == DOMAIN

    # ------------------------------------------------------------
    # Build explicit configs
    # ------------------------------------------------------------
    explicit: dict[str, LowpassCfg] = {}
    # pattern sources already filtered
    created_sources: set[str] = set()

    for item in sensors_list:
        _warn_unknown_keys("sensors[]", item, _ITEM_KEYS - {CONF_MATCH})
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

    # ------------------------------------------------------------
    # Initial pattern matching (executed after HA fully started)
    # ------------------------------------------------------------

    @callback
    def _full_rescan_after_start(_event):

        create_cfgs: dict[str, LowpassCfg] = {}   # sources to filter now
        keep_cfgs: dict[str, LowpassCfg] = {}      # sources that exist (cleanup keeps them)

        # ------------------------------------------------------------
        # Scan sources
        # ------------------------------------------------------------
        for p, pat in patterns:

            local_count = 0

            # Sources: registered sensors, and sensors with a state but no registry entry
            # (YAML sensors without unique_id), which the dynamic path below filters too
            reg = er.async_get(hass)
            sources = {e.entity_id for e in reg.entities.values() if e.domain == "sensor"}
            sources.update(hass.states.async_entity_ids("sensor"))

            for eid in sorted(sources):

                if eid in explicit or eid in keep_cfgs:
                    continue

                if not fnmatch.fnmatch(eid, pat):
                    continue

                # Recursion protection: never filter a lowpass_dt entity
                if _is_own(eid):
                    _LOGGER.warning("patterns[]: match=%r includes lowpass sensor %r. Skipped to avoid recursion.", pat, eid)
                    continue

                # -------- KEEP (existence only) --------
                keep_cfgs[eid] = build_cfg(
                    p,
                    source=eid,
                    allow_unique_id=False,
                    quiet=True,
                )

                # -------- CREATE (numeric sources only) --------
                st = hass.states.get(eid)
                if st is None or not _is_measurement(st):
                    continue

                local_count += 1

                if local_count > MAX_PATTERN_ENTITIES:
                    _LOGGER.warning("patterns[]: match=%r exceeded limit=%d. Aborted.", pat, MAX_PATTERN_ENTITIES)
                    break

                create_cfgs[eid] = keep_cfgs[eid]
                created_sources.add(eid)

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

            # find the source entity from unique_id
            if entity.unique_id not in keep_unique_ids:
                _LOGGER.warning("cleanup removing entity_id=%r unique_id=%r", entity.entity_id, entity.unique_id)
                reg.async_remove(entity.entity_id)

        # ------------------------------------------------------------
        # CREATE
        # ------------------------------------------------------------
        entities = []
        reg = er.async_get(hass)

        for cfg in create_cfgs.values():

            meta = make_meta(hass, cfg, is_pattern=True)
            desired_unique_ids.add(meta.unique_id)

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

    # Loaded while Home Assistant is starting: wait for every source to exist. Loaded once
    # it runs (a reload of the entry): scan now, 'homeassistant_started' will not come again.
    if hass.state is CoreState.running:
        _full_rescan_after_start(None)
    else:
        unsub = hass.bus.async_listen_once(
            "homeassistant_started",
            _full_rescan_after_start,
        )
        entry.async_on_unload(unsub)

    # ------------------------------------------------------------
    # Dynamic pattern matching
    # ------------------------------------------------------------

    pattern_dynamic_created: set[str] = set()
    limit_reported = False

    @callback
    def _maybe_add_new_entity(event):
        nonlocal limit_reported

        if hass.state != CoreState.running:
            return

        new_eid = event.data.get("entity_id")
        if not new_eid:
            return

        st = hass.states.get(new_eid)
        if not st:
            return

        if st.state in (None, "unknown", "unavailable") or not _is_measurement(st):
            return

        # (already filtered: nothing to build at each of its changes)
        if new_eid in explicit or new_eid in created_sources:
            return

        for p, pat in patterns:

            if not fnmatch.fnmatch(new_eid, pat):
                continue

            if _is_own(new_eid):
                return

            if len(pattern_dynamic_created) >= MAX_PATTERN_ENTITIES:
                if not limit_reported:
                    _LOGGER.error("patterns[]: dynamic creation aborted, limit=%d reached", MAX_PATTERN_ENTITIES)
                    limit_reported = True
                return

            cfg = build_cfg(
                p,
                source=new_eid,
                allow_unique_id=False,
                quiet=True,
            )

            meta = make_meta(hass, cfg, is_pattern=True)

            # unique_id protection (correct concept separation)
            if meta.unique_id in desired_unique_ids:
                created_sources.add(new_eid)
                return

            desired_unique_ids.add(meta.unique_id)
            pattern_dynamic_created.add(meta.unique_id)
            created_sources.add(new_eid)

            ent = sensor_cls(
                hass,
                cfg,
                is_pattern=True,
                precomputed=meta,
            )

            async_add_entities([ent])

            own_entity_ids.add(ent.entity_id)

            break

    unsub_state_changed = hass.bus.async_listen(
        "state_changed",
        _maybe_add_new_entity,
    )

    entry.async_on_unload(unsub_state_changed)