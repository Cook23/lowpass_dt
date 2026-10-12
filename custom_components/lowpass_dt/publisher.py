import logging
from types import SimpleNamespace
import math

_LOGGER = logging.getLogger(__name__)


# ------------------------------------------------------------
# Utility: derive rounding precision from fixed deadband with histeresis
# ------------------------------------------------------------
def _update_decimals(deadband: float | None, current_decimals: int | None) -> int:
    if deadband is None or deadband <= 0:
        return current_decimals if current_decimals is not None else 2

    # (first value: the middle of the hysteresis band below, where the following values
    # stay — d is kept while 10^-(d-0.5) <= deadband <= 10^-(d-1.5))
    if current_decimals is None:
        return min(6, max(0, math.floor(-math.log10(deadband) + 1.5)))

    threshold_up = 10 ** (-(current_decimals - 1 + 0.5))
    if deadband < threshold_up:
        return min(6, current_decimals + 1)

    threshold_down = 10 ** (-(current_decimals - 1 - 0.5))
    if deadband > threshold_down:
        return max(0, current_decimals - 1)

    return current_decimals


class Publisher:
    """Handle publishing filtered values and injected updates."""

    def __init__(self, sensor, cfg, core):
        self.sensor = sensor
        self.cfg = cfg
        self.core = core
        self.dt_silence = None  # storage for dt_silence passed by sensor

        # EMA for dt_output
        self.dt_output_mean = None
        self.dt_output_m2 = None

        self.clamped_to_source = False
        self.output_just_resumed = False
        # total_increasing: the source was reset, the next lower value is accepted once
        self.reset_pending = False

        self.decimals = None

    # ------------------------------------------------------------
    # Difference a - b (wrapped for circular sensors)
    # ------------------------------------------------------------
    def _diff(self, a, b):
        if self.cfg.circular is None:
            return a - b
        return ((a - b + self.cfg.circular / 2) % self.cfg.circular) - self.cfg.circular / 2

    # ------------------------------------------------------------
    # Convergence detection
    # ------------------------------------------------------------
    def _check_convergence(self, last_src, deadband):
        """The output has reached the source: within the deadband, or, below the displayed
        precision, equal to it once rounded (with `deadband: 0`, the only way it can)."""
        decimals = self.cfg.rounding if self.cfg.rounding is not None else self.decimals
        resolution = 0.5 * 10 ** -decimals if decimals is not None else 0.0
        return last_src is not None and abs(self._diff(self.core.y, last_src)) < max(deadband, resolution)

    # ------------------------------------------------------------
    # EMA helper
    # ------------------------------------------------------------
    def _update_dt_output_stats(self, dt_output):
        dt_output_sigma = None

        if dt_output is not None and not self.output_just_resumed:
            alpha = 0.1

            if self.dt_output_mean is None or self.dt_output_m2 is None:
                self.dt_output_mean = dt_output
                self.dt_output_m2 = dt_output * dt_output
            else:
                self.dt_output_mean = (
                    (1 - alpha) * self.dt_output_mean + alpha * dt_output
                )
                self.dt_output_m2 = (
                    (1 - alpha) * self.dt_output_m2
                    + alpha * (dt_output * dt_output)
                )

            # Compute sigma
            var = max(
                self.dt_output_m2
                - self.dt_output_mean * self.dt_output_mean,
                0.0,
            )
            dt_output_sigma = math.sqrt(var)

        return dt_output_sigma

    # ------------------------------------------------------------
    # Decide if a publish should occur
    # ------------------------------------------------------------
    def should_publish(self, now):
        """Decide if we should publish."""

        if self.core.y is None:
            return False

        if self.core.time_last_pub is None or self.sensor._attr_native_value is None:
            return True

        # deadband + integral correction
        deadband_eff = self.core.effective_deadband()

        self.core.err = self._diff(self.core.y, self.sensor._attr_native_value)

        # Integral correction: the current error times the time since the last write, so
        # that a lasting small error (a slow drift under the deadband) is written in the
        # end. Close enough to an accumulated integral on a filtered signal (measured:
        # the same writes and accuracy within a few %), and simpler.
        dt = max(0.0, now - self.core.time_last_pub)
        tau_i = max(1.0, self.cfg.tau)
        self.core.err_i = (self.core.err * dt) / tau_i

        if abs(self.core.err) >= deadband_eff or abs(self.core.err_i) >= deadband_eff:
            if self.cfg.max_rate_dt > 0:
                if (now - self.core.time_last_pub) > self.cfg.max_rate_dt or self.output_just_resumed:
                    return True
                else:
                    if self.cfg.deadband is None and self.core.t_sigma_start is not None:
                        elapsed = now - self.core.t_sigma_start
                        if elapsed >= self.cfg.deadband_tau_sigma:
                            _LOGGER.debug(
                                "Publish blocked by max_rate_dt=%.1fs for %r (deadband=%.6f, err=%.6f, err_i=%.6f)",
                                self.cfg.max_rate_dt,
                                self.cfg.source,
                                deadband_eff,
                                self.core.err,
                                self.core.err_i,
                            )
                    return False
            else:
                return True
        else:
            # periodic publish (build_cfg ensures min_rate_dt > max_rate_dt)
            if (now - self.core.time_last_pub) > self.cfg.min_rate_dt:
                return True

        return False

    # ------------------------------------------------------------
    # MAIN PUBLISH
    # ------------------------------------------------------------
    def publish(self, src_state, now, dt, force, injected):
        """Publish filtered value to Home Assistant.

        force: end-of-silence marker (the last source value, nudged so that the Recorder
        stores it). injected: a synthetic sample during a silence."""

        s = self.sensor
        inj = self.sensor.injector
        last_src = s._last_source_value

        if injected:
            self.output_just_resumed = True

        deadband = self.core.effective_deadband()

        # During a silence, once the output has converged to the last source value, it is
        # published as that value exactly (and, by default, the injection stops)
        converged = injected and self._check_convergence(last_src, deadband)

        if not force and not converged and not self.should_publish(now):
            return

        dt_output = None if self.core.time_last_pub is None else max(0.0, now - self.core.time_last_pub)
        dt_output_sigma = self._update_dt_output_stats(dt_output)

        s.sync_source_attributes(src_state.attributes or {})

        reported = float(self.core.y)

        was_clamped_to_source = self.clamped_to_source
        if converged:
            reported = float(last_src)
            self.clamped_to_source = True
            if self.cfg.silence not in ("zero", "0", "unknown"):
                inj.stop_injection()
                self.output_just_resumed = True
                self.clamped_to_source = False

        if force:
            reported = float(last_src)

        reported, decimals = self._round(reported, deadband)

        if force:
            reported = self._marker_value(reported, decimals)

        if self._breaks_monotonicity(reported):
            return

        # silence: zero / unknown — the plateau reached, the output shows 0 or nothing
        if was_clamped_to_source and self.cfg.silence in ("zero", "0", "unknown"):
            if self.cfg.silence in ("zero", "0") and s._attr_state_class not in ("total", "total_increasing"):
                s._attr_native_value = 0.0
            else:
                s._attr_native_value = None
            inj.stop_injection()
            self.output_just_resumed = True
            self.clamped_to_source = False
        else:
            s._attr_native_value = reported

        # a real sample published: the silence is over
        if not injected and not force:
            self.output_just_resumed = False

        s._attr_extra_state_attributes = (
            self._debug_attributes(dt, dt_output, dt_output_sigma)
            if self.cfg.debug
            else {"source": self.cfg.source}
        )

        self.core.time_last_pub = now
        s.async_write_ha_state()

    # ------------------------------------------------------------
    # Rounding (fixed, or derived from the deadband)
    # ------------------------------------------------------------
    def _round(self, value, deadband):
        if self.cfg.rounding is not None:
            decimals = self.cfg.rounding
        else:
            self.decimals = _update_decimals(deadband, self.decimals)
            decimals = self.decimals

        value = round(value, decimals)

        # A circular value stays within [0, period): 359.9996 rounds to 0, not 360
        if self.cfg.circular is not None:
            value = round(value % self.cfg.circular, decimals)
            if value >= self.cfg.circular:
                value = 0.0

        return value, decimals

    # ------------------------------------------------------------
    # End-of-silence marker value
    # ------------------------------------------------------------
    def _marker_value(self, reported, decimals):
        """The plateau's value, nudged below the displayed precision: Home Assistant would
        not record a state equal to the current one."""
        if self.cfg.silence == "unknown":
            value = 0.0
        elif self.cfg.silence in ("zero", "0"):
            value = 10 ** (-decimals - 1)
        else:
            value = reported + 10 ** (-decimals - 1)

        _LOGGER.debug(
            "end-of-silence marker in publisher for %s last_source_value=%f",
            self.sensor.entity_id,
            value,
        )
        return value

    # ------------------------------------------------------------
    # total_increasing: never publish a lower value, except after a reset
    # ------------------------------------------------------------
    def _breaks_monotonicity(self, reported):
        s = self.sensor
        prev = s._attr_native_value

        if s._attr_state_class != "total_increasing" or prev is None or reported >= prev:
            return False

        if self.reset_pending:
            _LOGGER.info(
                "total_increasing monotonicity break ACCEPTED after reset for %s: %.6f → %.6f",
                s.entity_id,
                prev,
                reported,
            )
            self.reset_pending = False
            return False

        _LOGGER.debug(
            "total_increasing monotonicity break BLOCKED for %s: %.6f → %.6f",
            s.entity_id,
            prev,
            reported,
        )
        return True

    # ------------------------------------------------------------
    # Debug attributes
    # ------------------------------------------------------------
    def _debug_attributes(self, dt, dt_output, dt_output_sigma):
        return {
            "source": self.cfg.source,
            "unique_id": self.sensor._unique_id_seed,

            "tau_filter": self.cfg.tau,
            "max_rate_dt": self.cfg.max_rate_dt,
            "min_rate_dt": self.cfg.min_rate_dt,
            "filter_output": float(self.core.y),

            "source_dt": {
                "source_dt": dt,
                "source_silence_3sigma": self.dt_silence,
                "silent": self.output_just_resumed,
            },

            "deadband": {
                "deadband": self.core.effective_deadband(),
                "deadband_tau_sigma": self.cfg.deadband_tau_sigma,
                **(
                    {"deadband_k_sigma": self.cfg.deadband_k_sigma}
                    if self.cfg.deadband is None
                    else {}
                ),
                "deadband_filtered_mean": self.core.src_mean,
                "deadband_filtered_sigma": self.core.src_sigma,
            },

            "dt_output": {
                "dt_output": dt_output,
                "dt_output_mean": self.dt_output_mean,
                "dt_output_sigma": dt_output_sigma,
            },
        }

    # ------------------------------------------------------------
    # Injected publication
    # ------------------------------------------------------------
    def publish_injected(self, last_source_value, now, dt, injected=True):
        src = self.sensor.hass.states.get(self.cfg.source)
        attrs = src.attributes if src else {}

        fake = SimpleNamespace(
            attributes=attrs,
            state=str(last_source_value),
        )

        self.publish(fake, now, dt, force=False, injected=injected)

    # ------------------------------------------------------------
    # Output followed between samples (not a silence): the usual publication rules
    # ------------------------------------------------------------
    def publish_tracked(self, last_source_value, now, dt):
        self.publish_injected(last_source_value, now, dt, injected=False)