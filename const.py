DOMAIN = "lowpass_dt"                             # integration domain name

CONF_SENSORS = "sensors"                          # list of explicit sensor configs
CONF_SOURCE = "source"                            # source entity_id for filtering
CONF_PATTERNS = "patterns"                        # list of pattern-based configs
CONF_MATCH = "match"                              # fnmatch pattern for auto-matching sensors

CONF_NAME = "name"                                # explicit friendly name override
CONF_PREFIX = "prefix"                            # prefix for generated entity_id
CONF_SUFFIX = "suffix"                            # suffix for generated friendly_name

CONF_UNIQUE_ID = "unique_id"                      # OPTIONAL: force unique_id (explicit sensors only)

CONF_TAU = "tau"                                  # low-pass time constant (seconds)
CONF_ROUND = "round"                              # rounding precision for output

CONF_DEADBAND = "deadband"                        # fixed deadband threshold (optional)
CONF_DEADBAND_TAU_SIGMA = "deadband_tau_sigma"    # tau for sigma estimator (default max(1000*tau, 10h))
CONF_DEADBAND_K_SIGMA = "deadband_k_sigma"        # adaptive deadband multiplier (default 2.0)

CONF_MIN_RATE_DT = "min_rate_dt"                  # max interval between outputs (seconds)
CONF_MAX_RATE_DT = "max_rate_dt"                  # min interval between outputs (rate limiter)