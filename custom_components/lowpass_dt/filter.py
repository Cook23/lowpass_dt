import logging
import math

_LOGGER = logging.getLogger(__name__)


def _alpha(dt, tau):
    """Weight of the value in effect over dt (zero-order hold): the exact first order,
    1 - exp(-dt/tau). Splitting an interval in several steps gives the same result, so
    the output does not depend on how often it is computed."""
    if tau <= 0:
        return 1.0
    return 1.0 - math.exp(-dt / tau)


class LowpassCore:
    """Pure math core: low-pass, adaptive sigma, deadband."""

    def __init__(self, cfg):
        self.cfg = cfg

        # circular period (None: a plain number): the configured one, or the one resolved
        # at runtime (an angle detected, or turned off for a total_increasing source)
        self.period = cfg.circular

        # filter state (circular: y is the direction of y_vec, the mean of unit vectors;
        # a new y sets it again, as the unit vector of y)
        self.y = None
        self.y_vec = None
        self.t_prev = None
        self.x_prev = None

        # stats (always computed for transparency)
        self.src_mean = None
        self.mean_vec = None
        self.src_var = None
        self.src_sigma = None
        self.src_m2 = None

        # sigma horizon start (None = just started / no restore)
        self.t_sigma_start = None

        # time of the last real sample: the σ statistics weigh real samples by the time
        # between them, whatever was computed in between (injection, tracking)
        self.t_last_real = None

    # ------------------------------------------------------------
    # Difference a - b (the shortest way around for a circular value)
    # ------------------------------------------------------------
    def diff(self, a, b):
        if self.period is None:
            return a - b
        return ((a - b + self.period / 2) % self.period) - self.period / 2

    def set_period(self, period):
        self.period = period

    @property
    def y(self):
        return self._y

    @y.setter
    def y(self, value):
        self._y = value
        self.y_vec = None

    @property
    def src_mean(self):
        return self._src_mean

    @src_mean.setter
    def src_mean(self, value):
        self._src_mean = value
        self.mean_vec = None

    # ------------------------------------------------------------
    # A circular mean moved toward a value, by weight alpha: as Home Assistant's statistics
    # average an angle (and history-explorer-card), each value a unit vector, the mean the
    # direction of their weighted sum — here an exponential weighting, the filter's.
    # ------------------------------------------------------------
    def _toward_on_circle(self, vec, mean, target, alpha):
        k = 2 * math.pi / self.period
        if vec is None:
            vec = (math.cos(k * mean), math.sin(k * mean))
        c = vec[0] + alpha * (math.cos(k * target) - vec[0])
        s = vec[1] + alpha * (math.sin(k * target) - vec[1])
        if c == 0.0 and s == 0.0:
            # (vectors cancelling exactly: no direction, the mean stays where it was)
            return mean, (c, s)
        return (math.atan2(s, c) / k) % self.period, (c, s)

    # ------------------------------------------------------------
    # Update filter from real source value
    # ------------------------------------------------------------
    def update_from_source(self, x, now):
        """Update filter from real source value."""
        if self.y is None:
            self.y = x % self.period if self.period is not None else x
            self.t_prev = now
            self.t_last_real = now
            self.x_prev = x
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
        dt = max(0.0, now - t_prev)
        alpha = _alpha(dt, tau)
        x_prev = self.x_prev if self.x_prev is not None else self.y

        # Zero-order hold (ZOH): dt[n] is the duration during which x_prev
        # (not the newly arrived x) was the actual value of the signal.
        if self.period is None:
            self._y = self._y + alpha * (x_prev - self._y)
        else:
            self._y, self.y_vec = self._toward_on_circle(self.y_vec, self._y, x_prev, alpha)

        self.t_prev = now
        self.x_prev = x

        # -------------------------
        # EMA of filtered signal
        # -------------------------

        # Start of the σ estimate, from which its time constant grows from 10 tau to
        # deadband_tau_sigma. Set here, not only on the very first sample: the sensor
        # gives the filter its value (last state, or the source's) before any sample.
        if self.t_sigma_start is None:
            self.t_sigma_start = now
        tau_s_min = 10.0 * tau
        tau_s_max = max(0.0, float(self.cfg.deadband_tau_sigma))
        elapsed = max(0.0, now - self.t_sigma_start)
        tau_s_dynamic = min(tau_s_max, max(tau_s_min, elapsed))

        dt_real = max(0.0, now - self.t_last_real) if self.t_last_real is not None else dt
        self.t_last_real = now
        beta = (dt_real / (tau_s_dynamic + dt_real)) if (tau_s_dynamic + dt_real) > 0 else 0.1

        if self.src_mean is None:
            self.src_mean = self.y
            self.src_m2 = self.y * self.y
            self.src_var = 0.0
        elif self.period is not None:
            # (σ: the output's shortest-way differences to the mean)
            err = self.diff(self._y, self._src_mean)
            self._src_mean, self.mean_vec = self._toward_on_circle(self.mean_vec, self._src_mean, self._y, beta)
            self.src_var = max((1 - beta) * self.src_var + beta * err * err, 0.0)
        else:
            self.src_m2 = self.src_var + self.src_mean ** 2
            self.src_mean = (1 - beta) * self.src_mean + beta * self.y
            self.src_m2 = (1 - beta) * self.src_m2 + beta * (self.y * self.y)
            self.src_var = max(self.src_m2 - self.src_mean ** 2, 0.0)

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
        dt = max(0.0, now - t_prev)
        alpha = _alpha(dt, tau)

        if self.period is None:
            self._y = self._y + alpha * (last_source_value - self._y)
        else:
            self._y, self.y_vec = self._toward_on_circle(self.y_vec, self._y, last_source_value, alpha)

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


