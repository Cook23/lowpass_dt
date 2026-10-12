from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from datetime import timedelta
from homeassistant.util import dt as dt_util
import math


class TauInjector:
    """Adaptive tau injector with clean silence detection (no polling)."""

    def __init__(self, hass, cfg, core, get_last_source, publish_callback, track_callback=None):
        self.hass = hass
        self.cfg = cfg
        self.core = core
        self.get_last_source = get_last_source
        self.publish_callback = publish_callback
        self.track_callback = track_callback

        # Tracking between samples: the output follows its known course toward the last
        # value as soon as it arrives, instead of waiting for the next sample (no pure delay
        # of one sample on slow sources). One-shot timer, re-armed while the output moves.
        self.unsub_track = None
        self.track_step = max(1.0, float(cfg.max_rate_dt), float(cfg.tau) / 100.0)

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
        # the output reached the last value during this silence: nothing more to inject
        self.converged = False

        # Manage unload
        self._stopped = False

    # ------------------------------------------------------------
    # Start (again, after stop: the entity can be removed and added again)
    # ------------------------------------------------------------
    def start(self):
        self._stopped = False

    # ------------------------------------------------------------
    # Public cleanup
    # ------------------------------------------------------------
    def stop(self):
        """Stop all timers (safe to call multiple times)."""
        
        self._stopped = True
        
        if self.unsub_silence is not None:
            self.unsub_silence()
            self.unsub_silence = None

        if self.unsub_injection is not None:
            self.unsub_injection()
            self.unsub_injection = None

        self._cancel_tracking()

        self.silent = False

    # ------------------------------------------------------------
    # Tracking between samples
    # ------------------------------------------------------------
    def _cancel_tracking(self):
        if self.unsub_track is not None:
            self.unsub_track()
            self.unsub_track = None

    def restart_tracking(self):
        self._cancel_tracking()
        if self._stopped or self.silent or self.track_callback is None:
            return
        self.unsub_track = async_call_later(self.hass, self.track_step, self._on_track)

    @callback
    def _on_track(self, _):
        self.unsub_track = None
        if self._stopped or self.silent:
            return
        last_source_value = self.get_last_source()
        if last_source_value is None or self.core.y is None:
            return

        now = dt_util.utcnow().timestamp()
        dt = self.core.update_synthetic(last_source_value, now)
        self.track_callback(last_source_value, now, dt)

        # keep following while the output is still away from the source by a deadband
        # (the rest, below the deadband, is left to the integral term and the silence)
        diff = self.core.y - last_source_value
        if self.cfg.circular is not None:
            diff = ((diff + self.cfg.circular / 2) % self.cfg.circular) - self.cfg.circular / 2
        if abs(diff) >= self.core.effective_deadband():
            self.unsub_track = async_call_later(self.hass, self.track_step, self._on_track)

    # ------------------------------------------------------------
    # Called at every real measurement
    # ------------------------------------------------------------
    def set_last_source_time(self, t):
        """Record timestamp of last real source update and update dt stats."""

        # Stop injector immediately when source speaks
        self.stop_injection()

        # End of silence
        self.silent = False
        self.converged = False

        # Cancel previous silence timer
        if self.unsub_silence is not None:
            self.unsub_silence()
            self.unsub_silence = None

        # First measurement after a silence (or a restart): counts as the silence limit in
        # the interval statistics. Not on a new entity (no statistics yet, the limit is still
        # tau): its first interval starts the statistics instead.
        if self.source_just_resumed and self.dt_mean is not None:
            self.t_last_source = t - self.limit

        self._update_dt_stats(t)

        self.source_just_resumed = False
        self.t_last_source = t

        # Schedule new silence detection, and follow the output until then (directly:
        # both timers fire later anyway, and a deferred call would start them a variable
        # time after the sample)
        self.restart_silence_timer()
        self.restart_tracking()

    # ------------------------------------------------------------
    # Update EMA stats for dt_source
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

        self._compute_limits()

    # ------------------------------------------------------------
    # Compute silence limit & injection interval
    # ------------------------------------------------------------
    def _compute_limits(self):
        tau = float(self.cfg.tau)

        if self.dt_mean is not None and self.dt_m2 is not None:
            var = max(self.dt_m2 - self.dt_mean ** 2, 0.0)
            std = math.sqrt(var)
            self.dt_silence_raw = self.dt_mean + 3.0 * std + 0.1
            self.limit = max(min(self.dt_silence_raw, tau), 1.0)
            self.interval = max(min(self.dt_mean, tau), 1.0)
        else:
            self.limit = tau
            self.interval = tau

    # ------------------------------------------------------------
    # Schedule silence one-shot
    # ------------------------------------------------------------
    def restart_silence_timer(self):
        if self._stopped or self.unsub_silence is not None:
            return
        self.unsub_silence = async_call_later(
            self.hass,
            self.limit,
            self._on_silence_detected,
        )

    # ------------------------------------------------------------
    # Silence detected (one-shot)
    # ------------------------------------------------------------
    # (@callback: Home Assistant runs a timer's plain function in a worker thread,
    # where it would change the filter state while the event loop uses it)
    @callback
    def _on_silence_detected(self, _):
        self.unsub_silence = None
        self.silent = True
        self.converged = False
        # (the source's next value ends a silence, whether the injection ran or not)
        self.source_just_resumed = True
        self._cancel_tracking()

        # Immediate injection
        self._inject_once()

        # Start periodic injection (unless that first injection already reached the value:
        # the next one would publish it again)
        self._start_periodic_injection()

    # ------------------------------------------------------------
    # Inject once
    # ------------------------------------------------------------
    def _inject_once(self):
        now = dt_util.utcnow().timestamp()
        last_source_value = self.get_last_source()

        if self._stopped:
            return

        if last_source_value is None:
            return

        dt = self.core.update_synthetic(last_source_value, now)

        self.publish_callback(last_source_value, now, dt)

    # ------------------------------------------------------------
    # Start periodic injection
    # ------------------------------------------------------------
    def _start_periodic_injection(self):
        if self._stopped or not self.silent or self.converged:
            return
        self.unsub_injection = async_track_time_interval(
            self.hass,
            self._tick,
            timedelta(seconds=self.interval),
        )

    # ------------------------------------------------------------
    # Stop periodic injection
    # ------------------------------------------------------------
    def stop_injection(self):
        self.converged = self.silent
        if self.unsub_injection is not None:
            self.unsub_injection()
            self.unsub_injection = None
            self.source_just_resumed = True

    # ------------------------------------------------------------
    # Periodic injection tick
    # ------------------------------------------------------------
    @callback
    def _tick(self, _):
        """Inject synthetic updates while silence persists."""

        if not self.silent:
            self.stop_injection()
            return

        self._inject_once()