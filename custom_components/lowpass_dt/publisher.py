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
    return max(0, int(math.ceil(-math.log10(deadband))) + 1)


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
        # NEW: ignore first dt_output after source resumes
        # ------------------------------------------------------------
        self.output_just_resumed = False

    # ------------------------------------------------------------
    # Convergence detection (NO LOGIC CHANGE)
    # ------------------------------------------------------------
    def _check_convergence(self, injected, last_src, deadband):
        return (
            injected
            and last_src is not None
            and abs(self.core.y - last_src) < deadband
        )

    # ------------------------------------------------------------
    # Apply convergence (NO LOGIC CHANGE)
    # ------------------------------------------------------------
    def _apply_convergence_if_needed(self, converged, last_src):
        if converged:
            reported = float(last_src)

            # STOP INJECTOR AFTER FINAL CONVERGENCE PUBLISH
            inj = self.sensor.injector
            inj._stop_injection()

            return reported

        return None

    # ------------------------------------------------------------
    # EMA helper (unchanged logic)
    # ------------------------------------------------------------
    def _update_dt_output_stats(self, dt_output):
        dt_output_sigma = None

        if dt_output is not None:
            if self.output_just_resumed:
                # Ignore first dt_output after source resumes
                self.output_just_resumed = False
            else:
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
    # MAIN PUBLISH
    # ------------------------------------------------------------
    def publish(self, src_state, now, dt, force, injected):
        """Publish filtered value to Home Assistant."""

        s = self.sensor
        inj = self.sensor.injector
        last_src = s._last_source_value

        # ------------------------------------------------------------
        # 1. Deadband
        # ------------------------------------------------------------
        deadband = self.core.effective_deadband()

        # ------------------------------------------------------------
        # 2. Convergence detection
        # ------------------------------------------------------------
        converged = self._check_convergence(
            injected,
            last_src,
            deadband,
        )

        # ------------------------------------------------------------
        # 3. Publication rule
        # ------------------------------------------------------------
        if not converged:
            if not self.core.should_publish(now, force):
                return

        # ------------------------------------------------------------
        # 4. Absolute rate limiting
        # ------------------------------------------------------------
        if self.cfg.max_rate_dt > 0:
            last_pub = self.core.t_last_pub
            if last_pub is not None and (now - last_pub) < self.cfg.max_rate_dt:
                if self.core.t_sigma_start is not None:
                    elapsed = now - self.core.t_sigma_start
                    if elapsed >= self.cfg.deadband_tau_sigma:
                        _LOGGER.warning(
                            "Lowpass: publish blocked by max_rate_dt (%.1fs) for %s",
                            self.cfg.max_rate_dt,
                            self.cfg.source,
                        )
                return

        attrs = src_state.attributes or {}

        # ------------------------------------------------------------
        # 5. Detect source resume ignore first dt_output
        # ------------------------------------------------------------
        if not injected and getattr(inj, "source_just_resumed", False):
            self.output_just_resumed = True
            inj.source_just_resumed = False

        # ------------------------------------------------------------
        # 6. Compute dt_output
        # ------------------------------------------------------------
        if self.core.t_last_pub is None:
            dt_output = None
        else:
            dt_output = now - float(self.core.t_last_pub)

        # ------------------------------------------------------------
        # 7. EMA(dt_output)
        # ------------------------------------------------------------
        dt_output_sigma = self._update_dt_output_stats(dt_output)

        # ------------------------------------------------------------
        # 8. Standard HA fields
        # ------------------------------------------------------------
        s._attr_native_unit_of_measurement = attrs.get("unit_of_measurement")
        s._attr_icon = attrs.get("icon")

        # Copy device_class (runtime safe)
        device_class = attrs.get("device_class")
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
            # No state_class from source → infer from device_class
            # Only set it once to avoid HA warnings
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
                    s._attr_state_class = "total_increasing"

        # ------------------------------------------------------------
        # 9. Default reported value (filtered)
        # ------------------------------------------------------------
        reported = float(self.core.y)

        if self.cfg.rounding is not None:
            decimals = self.cfg.rounding
        else:
            if s._attr_state_class == "total_increasing":
                decimals = 3
            else:
                decimals = _default_round_from_deadband(deadband)

        reported = round(reported, decimals)

        # ------------------------------------------------------------
        # 10. Apply convergence override if needed
        # ------------------------------------------------------------
        override = self._apply_convergence_if_needed(
            converged,
            last_src,
        )
        if override is not None:
            reported = override

        s._attr_native_value = reported

        # ------------------------------------------------------------
        # 11. Attributes
        # ------------------------------------------------------------
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
                "silent": inj.silent,
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
        # 12. Finalize
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