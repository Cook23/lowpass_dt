from __future__ import annotations

import fnmatch
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback, CoreState
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from . import const
from .const import (
    CONF_SENSORS,
    CONF_SOURCE,
    CONF_PATTERNS,
    CONF_MATCH,
    CONF_ATTRIBUTE,
    DOMAIN,
    ABSENT_SINCE_RUN,
)

from .config import (
    MAX_PATTERN_ENTITIES,
    LowpassCfg,
    CfgMeta,
    build_cfg,
    cfg_key,
    make_meta,
    parse_state,
)
from .attributes import attribute_value

_LOGGER = logging.getLogger(__name__)

_RUN_KEY = f"{DOMAIN}_run"


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

# Every known configuration key, taken from const.py (single source of truth)
_ITEM_KEYS = {v for k, v in vars(const).items() if k.startswith("CONF_")} - {CONF_SENSORS, CONF_PATTERNS}


def _warn_unknown_keys(where: str, item, allowed: set[str]) -> None:
    """Report keys that would otherwise be silently ignored (typos)."""
    if isinstance(item, dict):
        for key in item.keys() - allowed:
            _LOGGER.warning("%s: unknown key %r ignored.", where, key)


def _is_measurement(state, attribute: str | None = None) -> bool:
    """A state a pattern may filter: numeric, or not known yet (unknown). For an attribute:
    the entity has it, as a number."""
    if attribute:
        return attribute_value(state.entity_id, attribute, state.attributes)[0] is not None
    return state.state == "unknown" or parse_state(state.state)[0] is not None


def _pattern_attribute(p: dict) -> str | None:
    a = p.get(CONF_ATTRIBUTE)
    return a.strip() if isinstance(a, str) and a.strip() else None


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

    # This run of Home Assistant (the same across reloads of the entry, a new one at each
    # restart): an entity without a source is removed at the restart after it was found so
    run = hass.data.setdefault(_RUN_KEY, dt_util.utcnow().isoformat())

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
    # pattern sources already filtered (keys: see LowpassCfg.key)
    created_keys: set[str] = set()

    for item in sensors_list:
        _warn_unknown_keys("sensors[]", item, _ITEM_KEYS - {CONF_MATCH})
        source = _validate_sensor_item(item)
        if not source:
            continue

        # sensors/source chaining allowed
        cfg = build_cfg(
            item,
            source=source,
            allow_unique_id=True,
        )
        explicit[cfg.key] = cfg

    # ------------------------------------------------------------
    # Create explicit entities immediately (boot-safe)
    # ------------------------------------------------------------

    explicit_meta: dict[str, CfgMeta] = {}
    desired_unique_ids: set[str] = set()  # unique_id reference set

    for cfg in explicit.values():
        meta = make_meta(hass, cfg)
        explicit_meta[cfg.key] = meta
        desired_unique_ids.add(meta.unique_id)

    explicit_entities = [
        sensor_cls(hass, cfg, explicit_meta[cfg.key])
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
            attribute = _pattern_attribute(p)

            # Sources: registered sensors, and sensors with a state but no registry entry
            # (YAML sensors without unique_id), which the dynamic path below filters too.
            # For an attribute: entities of every domain (a weather, a climate).
            reg = er.async_get(hass)
            if attribute:
                sources = {e.entity_id for e in reg.entities.values()}
                sources.update(hass.states.async_entity_ids())
            else:
                sources = {e.entity_id for e in reg.entities.values() if e.domain == "sensor"}
                sources.update(hass.states.async_entity_ids("sensor"))

            for eid in sorted(sources):

                key = cfg_key(eid, attribute)
                if key in explicit or key in keep_cfgs:
                    continue

                if not fnmatch.fnmatch(eid, pat):
                    continue

                # Recursion protection: never filter a lowpass_dt entity
                if _is_own(eid):
                    _LOGGER.debug("patterns[]: match=%r includes lowpass sensor %r. Skipped to avoid recursion.", pat, eid)
                    continue

                # -------- KEEP (existence only) --------
                keep_cfgs[key] = build_cfg(
                    p,
                    source=eid,
                    allow_unique_id=False,
                    quiet=True,
                )

                # -------- CREATE (numeric sources only) --------
                st = hass.states.get(eid)
                if st is None or not _is_measurement(st, attribute):
                    continue

                local_count += 1

                if local_count > MAX_PATTERN_ENTITIES:
                    _LOGGER.warning("patterns[]: match=%r exceeded limit=%d. Aborted.", pat, MAX_PATTERN_ENTITIES)
                    break

                create_cfgs[key] = keep_cfgs[key]
                created_keys.add(key)

        # ------------------------------------------------------------
        # Compute keep_unique_ids
        # ------------------------------------------------------------
        keep_unique_ids: set[str] = set()

        for cfg in explicit.values():
            meta = make_meta(hass, cfg)
            keep_unique_ids.add(meta.unique_id)

        for cfg in keep_cfgs.values():
            meta = make_meta(hass, cfg)
            keep_unique_ids.add(meta.unique_id)

        # ------------------------------------------------------------
        # CLEANUP: an entity whose source is gone (or whose item left the configuration)
        # ------------------------------------------------------------
        # The first start without it, the entity is kept, not provided: Home Assistant
        # shows it "no longer provided by the integration". It is removed at the next
        # restart if still without a source — a source late to start keeps its entity.
        reg = er.async_get(hass)

        for entity in list(reg.entities.values()):
            if entity.config_entry_id != entry.entry_id:
                continue
            if entity.platform != DOMAIN:
                continue

            absent_since = (entity.options.get(DOMAIN) or {}).get(ABSENT_SINCE_RUN)
            if entity.unique_id in keep_unique_ids:
                if absent_since is not None:
                    reg.async_update_entity_options(entity.entity_id, DOMAIN, {})
                continue
            if absent_since is None:
                _LOGGER.info("cleanup: %r has no source, removed at the next restart if still so", entity.entity_id)
                reg.async_update_entity_options(entity.entity_id, DOMAIN, {ABSENT_SINCE_RUN: run})
            elif absent_since != run:
                _LOGGER.info("cleanup removing entity_id=%r unique_id=%r", entity.entity_id, entity.unique_id)
                reg.async_remove(entity.entity_id)

        # ------------------------------------------------------------
        # CREATE
        # ------------------------------------------------------------
        entities = []
        reg = er.async_get(hass)

        for cfg in create_cfgs.values():

            meta = make_meta(hass, cfg)
            desired_unique_ids.add(meta.unique_id)

            ent = sensor_cls(hass, cfg, meta)

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

        # (the first pattern matching wins, for the state and for each attribute)
        seen_keys: set[str] = set()

        for p, pat in patterns:

            if not fnmatch.fnmatch(new_eid, pat):
                continue

            if _is_own(new_eid):
                return

            attribute = _pattern_attribute(p)
            key = cfg_key(new_eid, attribute)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            if attribute:
                if not _is_measurement(st, attribute):
                    continue
            elif st.state in (None, "unknown", "unavailable") or not _is_measurement(st):
                continue

            # (already filtered: nothing to build at each of its changes)
            if key in explicit or key in created_keys:
                continue

            if len(pattern_dynamic_created) >= MAX_PATTERN_ENTITIES:
                if not limit_reported:
                    _LOGGER.warning("patterns[]: dynamic creation aborted, limit=%d reached", MAX_PATTERN_ENTITIES)
                    limit_reported = True
                return

            cfg = build_cfg(
                p,
                source=new_eid,
                allow_unique_id=False,
                quiet=True,
            )

            meta = make_meta(hass, cfg)

            # unique_id protection (correct concept separation)
            if meta.unique_id in desired_unique_ids:
                created_keys.add(key)
                continue

            desired_unique_ids.add(meta.unique_id)
            pattern_dynamic_created.add(meta.unique_id)
            created_keys.add(key)

            ent = sensor_cls(hass, cfg, meta)

            async_add_entities([ent])

            own_entity_ids.add(ent.entity_id)

    unsub_state_changed = hass.bus.async_listen(
        "state_changed",
        _maybe_add_new_entity,
    )

    entry.async_on_unload(unsub_state_changed)