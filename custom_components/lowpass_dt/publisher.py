import logging
from types import SimpleNamespace
import math

_LOGGER = logging.getLogger(__name__)


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
    return min(6, max(0, int(math.ceil(-math.log10(deadband))) + 1))


class Publisher:
    """Handle publishing filtered values and injected updates."""

    def __init__(self, sensor, cfg, core):
        self.sensor = sensor
        self.cfg = cfg
        self.core = core
        self.dt_silence = None  # storage for dt_silence passed by sensor

        # ------------------------------------------------------------
        # NEW: EMA for dt_output
        # ------------------------------------------------------------
        self.dt_output_mean = None
        self.dt_output_m2 = None

        # ------------------------------------------------------------
        # NEW: publish output source value done
        # ------------------------------------------------------------
        self.clamped_to_source = False

        # ------------------------------------------------------------
        # NEW: ignore first dt_output after source resumes
        # ------------------------------------------------------------
        self.output_just_resumed = False

    # ------------------------------------------------------------
    # Convergence detection
    # ------------------------------------------------------------
    def _check_convergence(self, last_src, deadband):

        if last_src is None:
            return False

        if self.cfg.circular is None:
            err = self.core.y - last_src
        else:
            err = (
                (self.core.y - last_src + self.cfg.circular / 2)
                % self.cfg.circular
            ) - self.cfg.circular / 2

        return abs(err) < deadband

    # ------------------------------------------------------------
    # EMA helper (unchanged logic)
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

        if self.core.time_last_pub is None or self.core.last_published is None:
            return True

        # periodic publish
        if self.cfg.min_rate_dt > self.cfg.max_rate_dt:
            if (now - self.core.time_last_pub) > self.cfg.min_rate_dt:
                return True

        # deadband + integral correction
        deadband_eff = self.core.effective_deadband()

        if self.cfg.circular is None:
            self.core.err = self.core.y - self.core.last_published
        else:
            self.core.err = (
                (self.core.y - self.core.last_published + self.cfg.circular / 2) % self.cfg.circular
            ) - self.cfg.circular / 2

        dt = max(0.0, now - self.core.time_last_pub)
        tau_i = max(1.0, self.cfg.tau)
        self.core.err_i = (self.core.err * dt) / tau_i

        if abs(self.core.err) >= deadband_eff or abs(self.core.err_i) >= deadband_eff:
            if self.cfg.max_rate_dt > 0:
                if (now - self.core.time_last_pub) > self.cfg.max_rate_dt or self.output_just_resumed:
                    return True
                else:
                    if self.core.t_sigma_start is not None:
                        elapsed = now - self.core.t_sigma_start
                        if elapsed >= self.cfg.deadband_tau_sigma:
                            _LOGGER.warning(
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
            return False

    # ------------------------------------------------------------
    # MAIN PUBLISH
    # ------------------------------------------------------------
    def publish(self, src_state, now, dt, force, injected):
        """Publish filtered value to Home Assistant."""

        s = self.sensor
        inj = self.sensor.injector
        last_src = s._last_source_value

        if injected:
            self.output_just_resumed = True

        # ------------------------------------------------------------
        # 1. Deadband
        # ------------------------------------------------------------
        deadband = self.core.effective_deadband()

        # ------------------------------------------------------------
        # 2. Convergence detection
        # ------------------------------------------------------------

        if injected:
            converged = self._check_convergence(last_src, deadband)
        else:
            converged = False

        # ------------------------------------------------------------
        # 3. Publication rule
        # ------------------------------------------------------------
        if not force and not converged and not self.should_publish(now):
            return

        attrs = src_state.attributes or {}

        # ------------------------------------------------------------
        # 4. Compute dt_output
        # ------------------------------------------------------------
        if self.core.time_last_pub is None:
            dt_output = None
        else:
            dt_output = max(0.0, now - self.core.time_last_pub)

        # ------------------------------------------------------------
        # 5. EMA(dt_output)
        # ------------------------------------------------------------
        dt_output_sigma = self._update_dt_output_stats(dt_output)

        # ------------------------------------------------------------
        # 6. Standard HA fields
        # ------------------------------------------------------------
        unit = attrs.get("unit_of_measurement")
        if unit is not None:
            s._attr_native_unit_of_measurement = unit

        icon = attrs.get("icon")
        if icon is not None:
            s._attr_icon = icon

        device_class = attrs.get("device_class")
        if device_class is not None:
            s._attr_device_class = device_class

        # Determine state_class:
        # 1) Use source state_class if provided
        # 2) Otherwise infer from device_class if provided

        # Source state_class (if explicitly provided by source)
        state_class = attrs.get("state_class")
        if state_class is not None:
            # Always trust explicit source state_class
            s._attr_state_class = state_class

        else:
            # No state_class from source → infer from device_class if defined
            if getattr(s, "_attr_state_class", None) is None:

                if device_class in (
                    "power",
                    "current",
                    "voltage",
                    "temperature",
                    "humidity",
                    "pressure",
                    "frequency",
                    "signal_strength",
                ):
                    s._attr_state_class = "measurement"

                elif device_class in (
                    "energy",
                    "gas",
                    "water",
                ):
                    s._attr_state_class = "total"


        # ------------------------------------------------------------

        reported = float(self.core.y)

        # ------------------------------------------------------------
        # 7. Apply override if needed
        # ------------------------------------------------------------

        was_clamped_to_source = self.clamped_to_source
        if converged:
            reported = float(last_src)
            self.clamped_to_source = True
            if self.cfg.silence not in ("zero", "0", "unknown"):
                inj._stop_injection()
                self.output_just_resumed = True
                self.clamped_to_source = False

        if force:
            reported = float(last_src)

        # ------------------------------------------------------------
        # 8. Round reported value (filtered)
        # ------------------------------------------------------------
        if self.cfg.rounding is not None:
            decimals = self.cfg.rounding
        else:
            decimals = _default_round_from_deadband(deadband)

        reported = round(reported, decimals)

        # ------------------------------------------------------------
        # 9. Hack to force record to recorder
        # ------------------------------------------------------------

        if force:
            if self.cfg.silence == "unknown":
                reported = 0.0
            elif self.cfg.silence in ("zero", "0"):
                reported = 10 ** (-decimals-1)
            else:
                reported = reported + 10 ** (-decimals-1)

            _LOGGER.debug(
                "end-of-silence marker in publisher for %s last_source_value=%f",
                s.entity_id,
                reported,
            )

        # ------------------------------------------------------------
        # 10. Monoticity
        # ------------------------------------------------------------

        prev = s._attr_native_value

        if (s._attr_state_class == "total_increasing" and prev is not None and reported < prev):
            if getattr(self.sensor, "_reset_pending", False):

                _LOGGER.warning(
                    "total_increasing monotonicity break ACCEPTED after reset for %s: %.6f → %.6f",
                    s.entity_id,
                    prev,
                    reported,
                )
                self.sensor._reset_pending = False

            else:
                _LOGGER.warning(
                    "total_increasing monotonicity break BLOCKED for %s: %.6f → %.6f",
                    s.entity_id,
                    prev,
                    reported,
                )

                return


        # ------------------------------------------------------------
        # 11. Unknown / Zero
        # ------------------------------------------------------------

        if was_clamped_to_source and self.cfg.silence in ("zero", "0", "unknown"):
            if self.cfg.silence in ("zero", "0") and s._attr_state_class not in ("total", "total_increasing"):
                s._attr_native_value = 0.0
            else:
                s._attr_native_value = None
            inj._stop_injection()
            self.output_just_resumed = True
            self.clamped_to_source = False
        else:
            s._attr_native_value = reported

        # ------------------------------------------------------------
        # 12. Reset output_just_resumed after successful real publish
        # ------------------------------------------------------------

        if not injected and not force:
            self.output_just_resumed = False

        # ------------------------------------------------------------
        # 13. Attributes
        # ------------------------------------------------------------

        if not self.cfg.debug:

            # minimal attributes
            s._attr_extra_state_attributes = {
                "source": self.cfg.source,
            }

        else:

            # full debug attributes
            s._attr_extra_state_attributes = {
                "source": self.cfg.source,
                "unique_id": s._unique_id_seed,

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
        # 14. Finalize
        # ------------------------------------------------------------
        self.core.finalize_publish(now)
        s.async_write_ha_state()

    # ------------------------------------------------------------
    # Injected publication (unchanged)
    # ------------------------------------------------------------
    def publish_injected(self, last_source_value, now, dt):
        src = self.sensor.hass.states.get(self.cfg.source)
        attrs = src.attributes if src else {}

        fake = SimpleNamespace(
            attributes=attrs,
            state=str(last_source_value),
        )

        self.publish(fake, now, dt, force=False, injected=True)