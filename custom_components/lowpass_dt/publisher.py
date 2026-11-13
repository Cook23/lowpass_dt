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
        # silence: zero / unknown shown in place of the plateau (kept across a restart), and
        # the source's value it fell silent on
        self.silence_shown = False
        self.silence_source = None
        self.output_just_resumed = False
        # total_increasing: the source was reset, the next lower value is accepted once
        self.reset_pending = False

        self.decimals = None

        # time of the last write, and the error of the output against it (deadband and
        # integral term)
        self.time_last_pub = None
        self.err = 0.0
        self.err_i = 0.0

    # ------------------------------------------------------------
    # silence: zero shown — the device is off, the value in effect is 0
    # ------------------------------------------------------------
    def holds_zero(self):
        return (
            self.silence_shown
            and self.cfg.silence in ("zero", "0")
            and self.sensor.state_class not in ("total", "total_increasing")
        )

    def source_resumed(self):
        """A real sample: the silence, and what it showed, is over."""
        self.clamped_to_source = False
        self.silence_shown = False
        self.silence_source = None

    # ------------------------------------------------------------
    # Convergence detection
    # ------------------------------------------------------------
    def check_convergence(self, last_src, deadband):
        """The output has reached the source: within the deadband, or, below the displayed
        precision, equal to it once rounded (with `deadband: 0`, the only way it can)."""
        decimals = self.cfg.rounding if self.cfg.rounding is not None else self.decimals
        resolution = 0.5 * 10 ** -decimals if decimals is not None else 0.0
        return last_src is not None and abs(self.core.diff(self.core.y, last_src)) < max(deadband, resolution)

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
    def should_publish(self, now, real=False):
        """Decide if we should publish.

        real: a real sample of the source — the only one that may pass max_rate_dt right
        after a silence (an injected or a tracked value never does: the injection would
        otherwise write at the source's rhythm until it converges)."""

        if self.core.y is None:
            return False

        if self.time_last_pub is None or self.sensor.native_value is None:
            return True

        # deadband + integral correction
        deadband_eff = self.core.effective_deadband()

        self.err = self.core.diff(self.core.y, self.sensor.native_value)

        # Integral correction: the current error times the time since the last write, so
        # that a lasting small error (a slow drift under the deadband) is written in the
        # end. Close enough to an accumulated integral on a filtered signal (measured:
        # the same writes and accuracy within a few %), and simpler.
        dt = max(0.0, now - self.time_last_pub)
        tau_i = max(1.0, self.cfg.tau)
        self.err_i = (self.err * dt) / tau_i

        if abs(self.err) >= deadband_eff or abs(self.err_i) >= deadband_eff:
            if self.cfg.max_rate_dt > 0:
                if (now - self.time_last_pub) > self.cfg.max_rate_dt or (real and self.output_just_resumed):
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
                                self.err,
                                self.err_i,
                            )
                    return False
            else:
                return True
        else:
            # periodic publish (build_cfg ensures min_rate_dt > max_rate_dt)
            if (now - self.time_last_pub) > self.cfg.min_rate_dt:
                return True

        return False

    # ------------------------------------------------------------
    # MAIN PUBLISH
    # ------------------------------------------------------------
    def publish(self, src_state, now, dt, force, injected, tracked=False):
        """Publish filtered value to Home Assistant.

        force: end-of-silence marker (the last source value, nudged so that the Recorder
        stores it). injected: a synthetic sample during a silence. tracked: the output
        followed between two samples."""

        s = self.sensor
        inj = self.sensor.injector
        last_src = s.held_value

        if injected:
            self.output_just_resumed = True

        deadband = self.core.effective_deadband()

        # During a silence, once the output has converged to the last source value, it is
        # published as that value exactly (and, by default, the injection stops)
        converged = injected and self.check_convergence(last_src, deadband)

        real = not injected and not force and not tracked
        if not force and not converged and not self.should_publish(now, real):
            return

        dt_output = None if self.time_last_pub is None else max(0.0, now - self.time_last_pub)
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

        # silence: zero / unknown — the plateau reached, the output shows 0 or nothing (at
        # the next injection only: a source resuming in between never writes it)
        if injected and was_clamped_to_source and self.cfg.silence in ("zero", "0", "unknown"):
            self.silence_source = last_src
            self.silence_shown = True
            if self.holds_zero():
                value = 0.0
                # (the device is off: the filter holds 0, and starts from it when the
                # source resumes — not from the value it fell silent on)
                self.core.y = 0.0
                self.core.x_prev = 0.0
            else:
                value = None
            inj.stop_injection()
            self.output_just_resumed = True
            self.clamped_to_source = False
        else:
            value = reported

        # a real sample published: the silence is over
        if not injected and not force:
            self.output_just_resumed = False
            self.silence_shown = False

        attributes = (
            self._debug_attributes(dt, dt_output, dt_output_sigma)
            if self.cfg.debug
            else {"source": self.cfg.source}
        )

        self.time_last_pub = now
        s.write_output(value, attributes)

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
        period = self.core.period
        if period is not None:
            value = round(value % period, decimals)
            if value >= period:
                value = 0.0

        return value, decimals

    # ------------------------------------------------------------
    # End-of-silence marker value
    # ------------------------------------------------------------
    def _marker_value(self, reported, decimals):
        """The value shown during the silence, nudged below the displayed precision: Home
        Assistant would not record a state equal to the current one. (With unknown shown,
        there is no plateau to close: no marker.)"""
        if self.silence_shown:
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
        prev = s.native_value

        if s.state_class != "total_increasing" or prev is None or reported >= prev:
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
            "unique_id": self.sensor.unique_id_seed,

            "tau_filter": self.cfg.tau,
            "max_rate_dt": self.cfg.max_rate_dt,
            "min_rate_dt": self.cfg.min_rate_dt,
            "filter_output": float(self.core.y),

            "source_dt": {
                "source_dt": dt,
                "source_silence_3sigma": self.dt_silence,
                "silent": self.sensor.injector.silent,
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

        self.publish(fake, now, dt, force=False, injected=injected, tracked=not injected)

    # ------------------------------------------------------------
    # Output followed between samples (not a silence): the usual publication rules
    # ------------------------------------------------------------
    def publish_tracked(self, last_source_value, now, dt):
        self.publish_injected(last_source_value, now, dt, injected=False)