from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback, Event
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.restore_state import ExtraStoredData
from homeassistant.util import dt as dt_util

from .const import DOMAIN

from .config import (
    LowpassCfg,
    CfgMeta,
    compute_name_and_slug,
)

from .filter import LowpassCore
from .injector import TauInjector
from .publisher import Publisher

_LOGGER = logging.getLogger(__name__)


# ------------------------------------------------------------
# Setup entry (wrapper): delegated to loader.py
# ------------------------------------------------------------
async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    from .loader import async_setup_entry_loader  # local import avoids circular import

    await async_setup_entry_loader(
        hass,
        entry,
        async_add_entities,
        sensor_cls=LowpassDtSensor,
    )

# ------------------------------------------------------------
# Extra-data
# ------------------------------------------------------------
class LowpassExtraData(ExtraStoredData):

    def __init__(self, data: dict):
        self._data = data

    def as_dict(self) -> dict:
        return self._data

    @classmethod
    def from_dict(cls, data: dict):
        return cls(data)

# ------------------------------------------------------------
# Low-pass sensor entity with tau injection and HA-native restore
# ------------------------------------------------------------
class LowpassDtSensor(SensorEntity, RestoreEntity):
    """Time-aware low-pass filter with optional tau injection."""

    _attr_should_poll = False

    def __init__(
        self,
        hass: HomeAssistant,
        cfg: LowpassCfg,
        is_pattern: bool,
        precomputed: CfgMeta | None = None,
    ) -> None:

        self.hass = hass
        self.cfg = cfg
        self.is_pattern = is_pattern

        self.core = LowpassCore(cfg)
        self.publisher = Publisher(self, cfg, self.core)

        self._last_source_value = None
        self._reset_pending = False

        self.injector = TauInjector(
            hass,
            cfg,
            self.core,
            lambda: self._last_source_value,
            self.publisher.publish_injected,
        )

        self._unsub_source = None
        self._unsub_name = None

        # ------------------------------------------------------------
        # Decide name_final, slug, use_name_mode
        # ------------------------------------------------------------
        if precomputed is not None:
            name_final = precomputed.name_final
            slug = precomputed.slug
            use_name = precomputed.use_name

            # ------------------------------------------------------------
            # UNIQUE_ID (NEW RULE – already computed in make_meta)
            # ------------------------------------------------------------
            self._attr_unique_id = precomputed.unique_id
            self._unique_id_seed = precomputed.unique_id_seed

        else:
            name_final, slug, use_name = compute_name_and_slug(
                hass,
                cfg,
                is_pattern,
            )

            # ------------------------------------------------------------
            # UNIQUE_ID (fallback path – should rarely happen)
            # ------------------------------------------------------------
            if getattr(cfg, "unique_id", None):
                seed = cfg.unique_id
            else:
                object_id_src = (
                    cfg.source.split(".", 1)[1]
                    if "." in cfg.source
                    else cfg.source
                )
                seed = f"{cfg.prefix}{object_id_src}"

            self._unique_id_seed = seed
            self._attr_unique_id = f"{DOMAIN}::{seed}"

        # ------------------------------------------------------------
        # ENTITY_ID / suggested_object_id
        # ------------------------------------------------------------
        if use_name:
            object_id = slug
        else:
            object_id_src = (
                cfg.source.split(".", 1)[1]
                if "." in cfg.source
                else cfg.source
            )
            object_id = f"{cfg.prefix}{object_id_src}"

        self._attr_suggested_object_id = object_id

        # ------------------------------------------------------------
        # FRIENDLY NAME
        # ------------------------------------------------------------
        self._attr_name = name_final
        self._use_name_mode = use_name

        # ------------------------------------------------------------
        # ATTRIBUTES
        # ------------------------------------------------------------

        self._attr_native_unit_of_measurement = None
        self._attr_state_class = None
        self._attr_device_class = None
        self._attr_native_value = None
        self._attr_extra_state_attributes = {}

    async def async_added_to_hass(self) -> None:
        """Restore internal state and register listeners."""

        # ------------------------------------------------------------
        # NEW HA-native restore
        # ------------------------------------------------------------

        await super().async_added_to_hass()

        # ------------------------------------------------------------
        # Restore extra_data
        # ------------------------------------------------------------
        data = await self.async_get_last_extra_data()

        if data:
            self._restore_internal_state(data.as_dict())
        else:
            if self.entity_id == f"sensor.{self._attr_suggested_object_id}":
                _LOGGER.warning("context lost or new entity for %r (empty filter state).", self.entity_id)

        # ------------------------------------------------------------
        # Rename via registry (deferred, safe)
        # ------------------------------------------------------------
        async def _deferred_rename():
            registry = er.async_get(self.hass)
            entry = registry.async_get(self.entity_id)

            if entry:
                desired_entity_id = f"sensor.{self._attr_suggested_object_id}"

                if entry.entity_id != desired_entity_id:
                    registry.async_update_entity(
                        entry.entity_id,
                        new_entity_id=desired_entity_id,
                    )

        self.hass.async_create_task(_deferred_rename())


        # ------------------------------------------------------------
        # Restore last visible state (robust + immediate write)
        # ------------------------------------------------------------
        last_state = await self.async_get_last_state()
        src = self.hass.states.get(self.cfg.source)

        restore_attrs = last_state.attributes if last_state else {}

        # Source may be unknown/unavailable but attributes can still exist
        source_attrs = {}
        if src is not None and isinstance(src.attributes, dict):
            source_attrs = src.attributes

        # ---- UNIT ----
        unit = restore_attrs.get("unit_of_measurement")
        if not unit:
            unit = source_attrs.get("unit_of_measurement")
        if unit:
            self._attr_native_unit_of_measurement = unit

        # ---- STATE CLASS ----
        state_class = restore_attrs.get("state_class")
        if not state_class:
            state_class = source_attrs.get("state_class")
        if state_class:
            self._attr_state_class = state_class

        # ---- DEVICE CLASS ----
        device_class = restore_attrs.get("device_class")
        if not device_class:
            device_class = source_attrs.get("device_class")
        if device_class:
            self._attr_device_class = device_class

        # ---- VALUE ----
        if last_state and last_state.state not in ("unknown", "unavailable"):
            try:
                self._attr_native_value = float(last_state.state)
            except (ValueError, TypeError):
                pass

        # ---- SOURCE via EXTRA_STATE ----
        self._attr_extra_state_attributes["source"] = self.cfg.source

        # ---- WRITE IMMEDIATELY IF STRUCTURE EXISTS ----
        if (
            self._attr_native_unit_of_measurement
            or self._attr_state_class
            or self._attr_device_class
        ):
            self.async_write_ha_state()
        else:
            _LOGGER.warning(
                "restore failed for %r — no structural attributes (restore=%s, source=%s)",
                self.entity_id,
                list(restore_attrs.keys()) if restore_attrs else [],
                list(source_attrs.keys()) if source_attrs else [],
            )

        # ------------------------------------------------------------
        # Dynamic naming only when name mode is not active
        # ------------------------------------------------------------
        if not self._use_name_mode:

            @callback
            def _update_name(_event):
                st2 = self.hass.states.get(self.cfg.source)
                if st2 is not None:
                    base2 = (st2.attributes or {}).get("friendly_name")
                    if base2 and not base2.startswith("sensor."):
                        self._attr_name = f"{base2} {self.cfg.suffix}"
                        self.async_write_ha_state()

                if self._unsub_name:
                    self._unsub_name()
                    self._unsub_name = None

            self._unsub_name = async_track_state_change_event(
                self.hass,
                [self.cfg.source],
                _update_name,
            )

        # ------------------------------------------------------------
        # Source state listener
        # ------------------------------------------------------------
        self._unsub_source = async_track_state_change_event(
            self.hass,
            [self.cfg.source],
            self._handle_source_event,
        )

        _LOGGER.debug("entity added: %s source=%s", self.entity_id, self.cfg.source)

    # ------------------------------------------------------------
    # Restore internal engine state (HA-native)
    # ------------------------------------------------------------
    def _restore_internal_state(self, data):

        core = self.core
        inj = self.injector
        pub = self.publisher

        # Low-pass core state
        lp = data.get("low_pass", {})
        core.y = lp.get("y")
        core.t_prev = lp.get("t_prev")
        core.time_last_pub = lp.get("time_last_pub")
        core.err_i = lp.get("err_i", 0.0)
        core.last_published = lp.get("last_published")

        # EMA filtered signal
        ema = data.get("ema_source", {})
        core.src_mean = ema.get("src_mean")
        core.src_m2 = ema.get("src_m2")
        core.t_sigma_start = ema.get("t_sigma_start")

        if core.src_mean is not None and core.src_m2 is not None:
            core.src_var = max(core.src_m2 - core.src_mean**2, 0.0)
            core.src_sigma = core.src_var ** 0.5

        # EMA dt_source
        dt_src = data.get("ema_dt_source", {})
        inj.dt_mean = dt_src.get("dt_mean")
        inj.dt_m2 = dt_src.get("dt_m2")
        inj.t_last_source = dt_src.get("t_last_source")

        # EMA dt_output
        dt_out = data.get("ema_dt_output", {})
        pub.dt_output_mean = dt_out.get("dt_output_mean")
        pub.dt_output_m2 = dt_out.get("dt_output_m2")

    # ------------------------------------------------------------
    # Export internal state (HA-native persistence)
    # ------------------------------------------------------------

    @property
    def extra_restore_state_data(self):
        data = {
            "low_pass": {
                "y": self.core.y,
                "t_prev": self.core.t_prev,
                "time_last_pub": self.core.time_last_pub,
                "err_i": self.core.err_i,
                "last_published": self.core.last_published,
            },
            "ema_source": {
                "src_mean": self.core.src_mean,
                "src_m2": self.core.src_m2,
                "t_sigma_start": self.core.t_sigma_start,
            },
            "ema_dt_source": {
                "dt_mean": self.injector.dt_mean,
                "dt_m2": self.injector.dt_m2,
                "t_last_source": self.injector.t_last_source,
            },
            "ema_dt_output": {
                "dt_output_mean": self.publisher.dt_output_mean,
                "dt_output_m2": self.publisher.dt_output_m2,
            },
        }

        return LowpassExtraData(data)

    # ------------------------------------------------------------
    # Handle real source updates
    # ------------------------------------------------------------
    @callback
    def _handle_source_event(self, event: Event) -> None:

        new_state = event.data.get("new_state")
        if new_state is None:
            return

        try:
            x = float(new_state.state)
        except Exception:
            return

        now = dt_util.utcnow().timestamp()

        # RESET detection (strong drop only)
        prev_src = self._last_source_value
        if (self._attr_state_class == "total_increasing" and prev_src is not None and x < prev_src * 0.5 ):
            _LOGGER.warning("total_increasing RESET detected for source %s dropped from %.6f to %.6f", self.entity_id, prev_src, x)

            # Hard reset of filter state
            self.core.y = x
            self.core.last_published = x
            self.core.err_i = 0.0
            self.core.t_prev = now

            self._reset_pending = True

        # Update last source value
        self._last_source_value = x

        # Update dt stats + stop injector
        self.injector.set_last_source_time(now)

        # Update filter with real measurement
        dt = self.core.update_from_source(x, now)

        # PASS dt_silence_raw TO PUBLISHER
        self.publisher.dt_silence = self.injector.dt_silence_raw

        # Publish real measurement
        self.publisher.publish(
            new_state,
            now,
            dt,
            force=False,
            injected=False,
        )