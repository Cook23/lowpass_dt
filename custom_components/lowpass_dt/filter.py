import logging
import math

_LOGGER = logging.getLogger(__name__)


class LowpassCore:
    """Pure math core: low-pass, adaptive sigma, deadband, rounding."""

    def __init__(self, cfg):
        self.cfg = cfg

        # filter state
        self.y = None
        self.t_prev = None

        # stats (always computed for transparency)
        self.src_mean = None
        self.src_var = None
        self.src_sigma = None
        self.src_m2 = None

        # last published (unrounded)
        self.last_published = None
        self.time_last_pub = None

        # error for deadband
        self.err = 0.0
        # integral error for deadband
        self.err_i = 0.0

        # sigma horizon start (None = just started / no restore)
        self.t_sigma_start = None

    # ------------------------------------------------------------
    # Update filter from real source value
    # ------------------------------------------------------------
    def update_from_source(self, x, now):
        """Update filter from real source value."""
        if self.y is None:
            self.y = x
            self.t_prev = now
            self.t_sigma_start = now

            # Always initialize stats
            self.src_mean = x
            self.src_m2 = x * x
            self.src_var = 0.0
            self.src_sigma = 0.0

            return 0.0

        # -------------------------
        # low-pass
        # -------------------------
        tau = max(0.0, float(self.cfg.tau))
        t_prev = self.t_prev if self.t_prev is not None else now
        dt = min(max(0.0, now - t_prev), tau)
        alpha = (dt / (tau + dt)) if (tau + dt) > 0 else 1.0

        self.y = self.y + alpha * (x - self.y)
        self.t_prev = now

        # -------------------------
        # EMA of filtered signal
        # -------------------------

        tau_lp = max(0.0, float(self.cfg.tau))
        tau_s_min = 10.0 * tau_lp
        tau_s_max = max(0.0, float(self.cfg.deadband_tau_sigma))

        if self.t_sigma_start is None:
            # just started (no restore context)
            tau_s_dynamic = tau_s_min
        else:
            elapsed = max(0.0, now - self.t_sigma_start)
            tau_s_dynamic = min(tau_s_max, max(tau_s_min, elapsed))

        beta = (dt / (tau_s_dynamic + dt)) if (tau_s_dynamic + dt) > 0 else 0.1

        y = self.y

        if self.src_mean is None or self.src_m2 is None:
            self.src_mean = y
            self.src_m2 = y * y
            self.src_var = 0.0
        else:
            self.src_mean = (1 - beta) * self.src_mean + beta * y
            self.src_m2 = (1 - beta) * self.src_m2 + beta * (y * y)
            self.src_var = max(self.src_m2 - self.src_mean * self.src_mean, 0.0)

        self.src_sigma = math.sqrt(self.src_var)

        return dt

    # ------------------------------------------------------------
    # Update filter using synthetic (injected) source value
    # ------------------------------------------------------------
    def update_synthetic(self, last_source_value, now):
        """Update filter using last real source value (injection)."""
        if self.y is None:
            return 0.0

        # low-pass
        tau = max(0.0, float(self.cfg.tau))
        t_prev = self.t_prev if self.t_prev is not None else now
        dt = min(max(0.0, now - t_prev), tau)
        alpha = (dt / (tau + dt)) if (tau + dt) > 0 else 1.0

        self.y = self.y + alpha * (last_source_value - self.y)
        self.t_prev = now

        return dt

    # ------------------------------------------------------------
    # Compute effective deadband (fixed or adaptive)
    # ------------------------------------------------------------
    def effective_deadband(self):
        if self.cfg.deadband is not None:
            return float(self.cfg.deadband)
        sigma = float(self.src_sigma) if self.src_sigma is not None else 0.0
        return max(0.001, float(self.cfg.deadband_k_sigma) * sigma)

    # ------------------------------------------------------------
    # Decide if a publish should occur
    # ------------------------------------------------------------
    def should_publish(self, now):
        """Decide if we should publish."""

        if self.y is None:
            return False

        if self.time_last_pub is None or self.last_published is None:
            return True

        # periodic publish
        if self.cfg.min_rate_dt > self.cfg.max_rate_dt:
            if (now - self.time_last_pub) > self.cfg.min_rate_dt:
                return True

        # deadband + integral correction
        deadband_eff = self.effective_deadband()
        self.err = self.y - self.last_published

        dt = max(0.0, now - self.time_last_pub)
        tau_i = max(1.0, self.cfg.tau)
        self.err_i = (self.err * dt) / tau_i

        if abs(self.err) >= deadband_eff or abs(self.err_i) >= deadband_eff:

            if self.cfg.max_rate_dt > 0:
                if (now - self.time_last_pub) > self.cfg.max_rate_dt:
                    return True
                else:
                    if self.t_sigma_start is not None:
                        elapsed = now - self.t_sigma_start
                        if elapsed >= self.cfg.deadband_tau_sigma:
                            _LOGGER.warning(
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
            return False

        return True

    # ------------------------------------------------------------
    # Finalize publish (update internal state)
    # ------------------------------------------------------------
    def finalize_publish(self, now):
        self.last_published = self.y
        self.time_last_pub = now

    # ------------------------------------------------------------
    # Persistence: export minimal state
    # ------------------------------------------------------------
    def export_state(self):
        return {
            "last_published": self.last_published,
            "time_last_pub": self.time_last_pub,
        }

    # ------------------------------------------------------------
    # Persistence: import minimal state
    # ------------------------------------------------------------
    def import_state(self, state):
        self.last_published = state.get("last_published")
        self.time_last_pub = state.get("time_last_pub")
