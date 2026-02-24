import math

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

        # adaptive deadband flag
        self.adaptive_deadband = (cfg.deadband is None)

        # last published (unrounded)
        self.last_published = None
        self.t_last_pub = None

        # integral error
        self.err_i = 0.0

    # ------------------------------------------------------------
    # Update filter from real source value
    # ------------------------------------------------------------
    def update_from_source(self, x, now):
        """Update filter from real source value."""
        if self.y is None:
            self.y = x
            self.t_prev = now

            # Always initialize stats
            self.src_mean = x
            self.src_m2 = x * x
            self.src_var = 0.0
            self.src_sigma = 0.0

            return 0.0, 1.0

        # -------------------------
        # low-pass
        # -------------------------
        tau = max(0.0, float(self.cfg.tau))
        t_prev = self.t_prev if self.t_prev is not None else now
        dt = min(max(0.0, now - t_prev), tau)
        alpha = (dt / (tau + dt)) if (tau + dt) > 0 else 1.0

        self.y = float(self.y) + alpha * (x - float(self.y))
        self.t_prev = now

        # -------------------------
        # EMA of filtered signal
        # -------------------------
        # Always update stats (even if deadband is fixed)
        tau_s = max(0.0, float(self.cfg.deadband_tau_sigma))
        beta = (dt / (tau_s + dt)) if (tau_s + dt) > 0 else 0.1
        y = float(self.y)

        if self.src_mean is None or self.src_m2 is None:
            self.src_mean = y
            self.src_m2 = y * y
            self.src_var = 0.0
        else:
            self.src_mean = (1 - beta) * self.src_mean + beta * y
            self.src_m2 = (1 - beta) * self.src_m2 + beta * (y * y)
            self.src_var = max(self.src_m2 - self.src_mean * self.src_mean, 0.0)

        self.src_sigma = math.sqrt(self.src_var)

        return dt, alpha

    # ------------------------------------------------------------
    # Update filter using synthetic (injected) source value
    # ------------------------------------------------------------
    def update_synthetic(self, last_source_value, now):
        """Update filter using last real source value (injection)."""
        if self.y is None:
            return 0.0, 1.0

        # low-pass
        tau = max(0.0, float(self.cfg.tau))
        t_prev = self.t_prev if self.t_prev is not None else now
        dt = min(max(0.0, now - t_prev), tau)
        alpha = (dt / (tau + dt)) if (tau + dt) > 0 else 1.0

        self.y = float(self.y) + alpha * (last_source_value - float(self.y))
        self.t_prev = now

        return dt

    # ------------------------------------------------------------
    # Compute effective deadband (fixed or adaptive)
    # ------------------------------------------------------------
    def effective_deadband(self):
        if self.cfg.deadband is not None:
            return float(self.cfg.deadband)
        sigma = float(self.src_sigma) if self.src_sigma is not None else 0.0
        return max(0.0, float(self.cfg.deadband_k_sigma) * sigma)

    # ------------------------------------------------------------
    # Decide if a publish should occur
    # ------------------------------------------------------------
    def should_publish(self, now, force):
        """Decide if we should publish."""
        if self.y is None:
            return False

        deadband_eff = self.effective_deadband()

        # periodic publish
        periodic_due = False
        if self.cfg.min_rate_dt != 0:
            if self.t_last_pub is None:
                periodic_due = True
            else:
                periodic_due = (now - float(self.t_last_pub)) >= float(self.cfg.min_rate_dt)

        # deadband + integral correction
        in_deadband = False
        if (deadband_eff is not None) and (self.last_published is not None):
            err = float(self.y) - float(self.last_published)
            dt = max(0.0, now - float(self.t_last_pub)) if self.t_last_pub is not None else 0.0

            tau_i = max(1e-6, float(self.cfg.tau))

            if abs(err) < deadband_eff:
                self.err_i += (err * dt) / tau_i
            else:
                self.err_i = 0.0

            in_deadband = (abs(err) < deadband_eff) and (abs(self.err_i) < deadband_eff)

        if (not force) and in_deadband and (not periodic_due):
            return False

        return True

    # ------------------------------------------------------------
    # Finalize publish (update internal state)
    # ------------------------------------------------------------
    def finalize_publish(self, now):
        self.last_published = float(self.y)
        self.t_last_pub = now
        self.err_i = 0.0

    # ------------------------------------------------------------
    # Persistence: export minimal state
    # ------------------------------------------------------------
    def export_state(self):
        return {
            "last_published": self.last_published,
            "t_last_pub": self.t_last_pub,
            "err_i": self.err_i,
        }

    # ------------------------------------------------------------
    # Persistence: import minimal state
    # ------------------------------------------------------------
    def import_state(self, state):
        self.last_published = state.get("last_published")
        self.t_last_pub = state.get("t_last_pub")
        self.err_i = state.get("err_i", 0.0)