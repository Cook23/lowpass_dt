from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback, Event
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity
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
        # Additional warning: name + prefix/suffix
        # ------------------------------------------------------------
        if use_name:
            if cfg.prefix != "lp_" or cfg.suffix != "(Filtered)":
                _LOGGER.warning(
                    "Lowpass: 'prefix' or 'suffix' ignored because 'name' is defined for sensor %s.",
                    cfg.source,
                )

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
        self._attr_entity_id = f"sensor.{object_id}"

        # ------------------------------------------------------------
        # FRIENDLY NAME
        # ------------------------------------------------------------
        self._attr_name = name_final
        self._use_name_mode = use_name

    # ------------------------------------------------------------
    # FIX: restore registry rename hook
    # ------------------------------------------------------------
    @callback
    def async_registry_entry_updated(self) -> None:
        reg = er.async_get(self.hass)
        entry = reg.async_get(self.entity_id)
        if entry is None:
            return

        desired = f"sensor.{self._attr_suggested_object_id}"
        if entry.entity_id != desired:
            try:
                reg.async_update_entity(
                    entry.entity_id,
                    new_entity_id=desired,
                )
            except Exception:
                pass

    async def async_added_to_hass(self) -> None:
        """Restore internal state and register listeners."""

        # ------------------------------------------------------------
        # NEW HA-native restore (extra_restore_data)
        # ------------------------------------------------------------
        await super().async_added_to_hass()
        data = await self.async_get_last_extra_data()
        if data:
            self._restore_internal_state(data)

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

        _LOGGER.debug(
            "LP entity added: %s source=%s",
            self.entity_id,
            self.cfg.source,
        )

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
        core.t_last_pub = lp.get("t_last_pub")
        core.err_i = lp.get("err_i", 0.0)
        core.last_published = lp.get("last_published")

        # EMA filtered signal
        ema = data.get("ema_source", {})
        core.src_mean = ema.get("src_mean")
        core.src_m2 = ema.get("src_m2")
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
    async def async_get_extra_restore_data(self):
        return {
            "low_pass": {
                "y": self.core.y,
                "t_prev": self.core.t_prev,
                "t_last_pub": self.core.t_last_pub,
                "err_i": self.core.err_i,
                "last_published": self.core.last_published,
            },
            "ema_source": {
                "src_mean": self.core.src_mean,
                "src_m2": self.core.src_m2,
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

        # Update last source value
        self._last_source_value = x

        # Update dt stats + stop injector
        self.injector.set_last_source_time(now)

        # Update filter with real measurement
        dt, _alpha = self.core.update_from_source(x, now)

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