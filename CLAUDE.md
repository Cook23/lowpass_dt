# lowpass_dt — notes for Claude sessions

Home Assistant custom integration (HACS): a time-aware low-pass filter for sensors.
Code in `custom_components/lowpass_dt/`; user-facing changes go in `CHANGELOG.md` and the
README "Version highlights".

Two lines are maintained: `main` (stable 1.3.x) and `v1.4-dev` (1.4 beta). Read `BRANCH.md`
on the branch you work on first. Work branches for pull requests: `claude/v1.3-port` (from
`main`) and `claude/v1.4-dev-port` (from `v1.4-dev`). Tests: see `tests/README.md`.

## Versions (same rules as history-explorer-card)

- **Release version** `1.4.x` (`1.3.x` on `main`): set in `manifest.json`, the CHANGELOG
  and the PR title (`1.4.x: …`) by the merge that releases it. `x` goes up by one at each
  release, and only then. A PR that releases nothing (docs, tests) keeps the version and is
  titled `Docs: …` / `Tests: …`. 1.4 releases are betas (GitHub pre-releases).
- **Development build** `1.4.xbyyy` (`x` = the next release): `yyy` goes up by one at each
  change of the code — the versioning follows the code: a commit that changes only
  documentation (`.md` files) keeps the build number and is not a build. Used only while
  developing and in discussions with the user, never in files: not in code comments,
  `manifest.json`, the CHANGELOG or commit and PR titles.
- **Work branches** (`claude/v1.3-port`, `claude/v1.4-dev-port`) are reset to their line
  after each merge. Nothing is merged without the user asking.
- **Fixes are made on 1.4 first.** They are backported to 1.3 only once tested in real use,
  and only at the user's request.

## Shared logic with history-explorer-card

[Cook23/history-explorer-card](https://github.com/Cook23/history-explorer-card) (a Lovelace
card, JavaScript) is a different project by the same author, but it reuses two pieces of
logic from this integration and documents them as "the same as lowpass_dt". A change to
either piece here must be mirrored there (and vice versa), or the docs on both sides become
wrong. The card is worked on in its own Claude session; tell the user when a change here
needs a matching change in the card.

Rule set by the user: each session manages its own repository's code. A session may write
`.md` files in the other repository only with the user's explicit approval, and never
modifies the other repository's code. A code change needed on the other side is passed to
that session (or to the user), not made from here.

Card files: in 1.1 (branch `main`) both functions below are in `src/history-explorer-card.js`;
in 1.2 (branch `v1.2-dev`) they are in `src/card-datasets.js`.

The card's session cannot send messages to this one (its `send_message` tool is missing).
To get its answers, read its transcript with `list_events` (session "GitHub History
Explorer"), or the user pastes them here.

### 1. `circular` option values — must stay identical

| here | card |
|---|---|
| `config.py` (parsing), `sensor.py` `_resolve_auto_circular` (auto-detection) | `_circularPeriod` |

- absent / `null` / `none` (trimmed, any case): automatic — state class `measurement_angle`,
  or a unit of exactly `°` (not `°C`/`°F`), gives a period of 360;
- `false`: never circular;
- a number or numeric string: that period; `2pi` (any case, all spaces removed): 2π;
- anything else (including `true`), or a period ≤ 0 or non-finite: off, with a warning.

Introduced in lowpass_dt v1.3.17 / card v1.1.46. Both READMEs and CHANGELOGs say "same values as" the other.

### 2. Silence detection — same rule, with known, deliberate differences

| here | card |
|---|---|
| `injector.py` `_update_dt_stats`, `_compute_limits`, `set_last_source_time` | `_applySilencePlateaus` (`lineMode: smart`) |

Common rule: the source's usual interval is an EMA (alpha 0.1) of the intervals between
values, with an EMA of their square for σ. An interval longer than `mean + 3σ + 0.1 s`
(never under 1 s) is a silence, and the first sample after a silence counts as that limit
in the EMA, not as its real length.

Differences (keep them in mind before calling the two "identical"):
- **Seed.** Here the EMA starts from the first interval, with σ = 0, because the filter runs
  online and the state is restored across restarts. The card has the whole history, so it
  starts from the median of the intervals, with σ from their median absolute deviation
  × 1.4826. The card's README says "started from their median".
- **Cap.** Here the silence limit is also capped at `tau` (`max(min(raw, tau), 1)`), because
  it drives the injection of synthetic samples. The card has no `tau` and does not cap it.
- The end-of-silence marker (v1.3.14) and the `silence` option (`none`/`last`/`zero`/`unknown`)
  exist only here.

Review of the silence detection (October 2026), decided by the user:
- After a Home Assistant restart, the downtime is already kept out of the EMA: on restore,
  `sensor.py` sets `injector.source_just_resumed`, so the first sample counts as the limit
  (same as after a silence, `injector.py` `set_last_source_time`). Not an issue, nothing to do.
- The +0.1 s margin is absolute: a very regular source (σ close to 0) can get a false
  silence from a delay of a few tenths of a second. Here that only means an extra injected
  sample, maybe published (and maybe an end-of-silence marker); the filtered value barely
  moves, since injection holds the last value as ZOH would. Left as is. A relative margin
  (e.g. `max(0.1 s, 5 % of the mean)`) would have to change in the card too.
- `state_reported` is not listened to: a sensor repeating the same value is most likely
  dead, and treating it as silent is right.
