from homeassistant.helpers.event import async_call_later, async_track_time_interval
from datetime import timedelta
from homeassistant.util import dt as dt_util
import math


class TauInjector:
    """Adaptive tau injector with clean silence detection (no polling)."""

    def __init__(self, hass, cfg, core, get_last_source, publish_callback):
        self.hass = hass
        self.cfg = cfg
        self.core = core
        self.get_last_source = get_last_source
        self.publish_callback = publish_callback

        # Periodic injection timer
        self.unsub_injection = None

        # Silence one-shot timer
        self.unsub_silence = None

        # Stats
        self.t_last_source = None
        self.dt_mean = None
        self.dt_m2 = None
        self.dt_silence_raw = None

        # First measurement after silence must be ignored
        self.source_just_resumed = False

        # Cached interval (updated only when source speaks)
        self.limit = float(cfg.tau)
        self.interval = float(cfg.tau)

        # Explicit silence state
        self.silent = False

    # ------------------------------------------------------------
    # Public cleanup
    # ------------------------------------------------------------
    def stop(self):
        """Stop all timers (safe to call multiple times)."""
        if self.unsub_silence is not None:
            self.unsub_silence()
            self.unsub_silence = None

        if self.unsub_injection is not None:
            self.unsub_injection()
            self.unsub_injection = None

        self.silent = False

    # ------------------------------------------------------------
    # Called at every real measurement
    # ------------------------------------------------------------
    def set_last_source_time(self, t):
        """Record timestamp of last real source update and update dt stats."""

        # Stop injector immediately when source speaks
        self._stop_injection()

        # End of silence
        self.silent = False

        # Cancel previous silence timer
        if self.unsub_silence is not None:
            self.unsub_silence()
            self.unsub_silence = None

        # First measurement after silence: ignore dt
        if self.source_just_resumed:
            self.t_last_source = t
            return

        # Update stats
        self._update_dt_stats(t)

        self.t_last_source = t

        # Schedule new silence detection
        self._schedule_silence_timer()

    # ------------------------------------------------------------
    # Update EMA stats for dt_source (NO LOGIC CHANGE)
    # ------------------------------------------------------------
    def _update_dt_stats(self, t):
        if self.t_last_source is None:
            return

        dt = t - self.t_last_source
        if dt <= 0:
            return

        alpha = 0.1

        if self.dt_mean is None or self.dt_m2 is None:
            self.dt_mean = dt
            self.dt_m2 = dt * dt
        else:
            self.dt_mean = (1 - alpha) * self.dt_mean + alpha * dt
            self.dt_m2 = (1 - alpha) * self.dt_m2 + alpha * (dt * dt)

        var = max(self.dt_m2 - self.dt_mean * self.dt_mean, 0.0)
        std = math.sqrt(var)

        dt_silence = self.dt_mean + 3.0 * std
        self.dt_silence_raw = dt_silence

        self._compute_limits()

    # ------------------------------------------------------------
    # Compute silence limit & injection interval (NO LOGIC CHANGE)
    # ------------------------------------------------------------
    def _compute_limits(self):
        tau = float(self.cfg.tau)

        self.limit = max(min(self.dt_silence_raw, tau), 1.0)
        self.interval = max(min(self.dt_mean, tau), 1.0)

    # ------------------------------------------------------------
    # Schedule silence one-shot
    # ------------------------------------------------------------
    def _schedule_silence_timer(self):
        self.unsub_silence = async_call_later(
            self.hass,
            self.limit,
            self._on_silence_detected,
        )

    # ------------------------------------------------------------
    # Silence detected (one-shot)
    # ------------------------------------------------------------
    def _on_silence_detected(self, _):
        self.unsub_silence = None
        self.silent = True

        # Immediate injection
        self._inject_once()

        # Start periodic injection
        self._start_periodic_injection()

    # ------------------------------------------------------------
    # Inject once (NO LOGIC CHANGE)
    # ------------------------------------------------------------
    def _inject_once(self):
        now = dt_util.utcnow().timestamp()
        last_source_value = self.get_last_source()

        if last_source_value is None:
            return

        dt = self.core.update_synthetic(last_source_value, now)

        self.hass.loop.call_soon_threadsafe(
            self.publish_callback,
            last_source_value,
            now,
            dt,
        )

    # ------------------------------------------------------------
    # Start periodic injection
    # ------------------------------------------------------------
    def _start_periodic_injection(self):
        self.unsub_injection = async_track_time_interval(
            self.hass,
            self._tick,
            timedelta(seconds=self.interval),
        )

    # ------------------------------------------------------------
    # Stop periodic injection
    # ------------------------------------------------------------
    def _stop_injection(self):
        if self.unsub_injection is not None:
            self.unsub_injection()
            self.unsub_injection = None
            self.source_just_resumed = True

    # ------------------------------------------------------------
    # Periodic injection tick
    # ------------------------------------------------------------
    def _tick(self, _):
        """Inject synthetic updates while silence persists."""

        if not self.silent:
            self._stop_injection()
            return

        self._inject_once()