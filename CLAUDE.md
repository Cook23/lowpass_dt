# lowpass_dt — notes for Claude sessions

Home Assistant custom integration (HACS): a time-aware low-pass filter for sensors.
Code in `custom_components/lowpass_dt/`, organized as described in `ARCHITECTURE.md` (keep it
up to date). User-facing changes go in `CHANGELOG.md`; the README "Version highlights" lists
new features only, never bug fixes.

Two lines are maintained: `main` (stable 1.3.x) and `v1.4-dev` (1.4 beta). Read `BRANCH.md`
on the branch you work on first. Work branches for pull requests: `claude/v1.3-port` (from
`main`) and `claude/v1.4-dev-port` (from `v1.4-dev`). Tests: see `tests/README.md`.

## What the filter is for (set by the user)

- A first-order low-pass that behaves as one although the sampling is extremely
  irregular; few writes, so as not to flood the Recorder; and a curve that looks natural
  and realistic when redrawn from the stored points, by tools the user does not control
  (straight lines or splines — hence the end-of-silence marker) as by history-explorer.
- `tau` is the time constant of the physical system measured (or the time below which an
  event is not worth recording), never derived from the source's period. The filter works
  properly when `tau` is at least about 10 times the source's usual period: that is the
  normal regime, where all of the above must hold. When the source slows down beyond `tau`, the
  low-pass no longer filters anything useful, but the output must stay sound: within the
  source's range, no overshoot, reaching the source's value, no flood of writes.
- It is home automation: what is interesting happens over tens of seconds to hours (a
  minute is a good average) — a house's temperature is roughly a first order of several
  hours or days; a few seconds of power consumption do not change the monthly bill. The
  deadband is a filter against what is "not significant", a deliberately loose criterion:
  write what matters, not what does not, set up by itself, without the user ever saying
  what matters. Not flight controls, not plate tectonics: keep the precision and the
  machinery in proportion.
- The deadband is there so as not to flood the Recorder; it has nothing to do with the
  measurement's uncertainty. What is significant is a change of state of the physical
  system (a big consumer starting): it must be written at once. While the signal just
  "lives", a point now and then. No static error: a source that holds still ends up
  published as is (the integral term). The adaptive deadband follows the amplitude of the
  system's own variations (σ of the filtered output, over up to `deadband_tau_sigma`).
- `tests/test_behavior.py` checks both regimes against the exact first-order filter.
- Decided by the user (October 2026), not to be raised again:
  - A new entity needs some time for everything to tune itself (σ of its intervals starts at
    0): a few false silences, and a few raw values written, at its start are expected.
  - With `deadband: 0` and no `round`, the output keeps 2 decimals on purpose: sources often
    have too many, and the convergence after a silence would take a ridiculously long time
    at their full precision. `round:` is there to impose another precision.

## Versions (same rules as history-explorer-card) — set by the user, not negotiable

- **A release publishes the merged files as they are.** The user releases the line's branch,
  under the version it carries; the release changes nothing in the files. So every merge must
  be publishable as is: its version number, CHANGELOG and docs final — no build number, no
  `[Unreleased]`, nothing left to fix at the release.
- **The version is the software's and its docs'.** A merge that touches neither the source nor
  the docs — only Claude's own files (`CLAUDE.md`, `BRANCH.md`) — keeps the version as it is,
  released or not.
- **The version number is the last release's + 1.** Before each merge, check on GitHub which
  version was released last (its releases) and which versions are dead (its tags: a tag
  without a release); the version merged — everywhere it's written: `manifest.json` and
  the CHANGELOG — must be the last release + 1, past any dead version (`1.4.3` released → `1.4.4`). Several merges can carry
  it, until the user releases it. A released number is never carried again.
- **A dead version.** The user says when a version is dead (GitHub sometimes fails a release):
  the version goes to the next number — everywhere it's written (`manifest.json`, and any source that carries it), the CHANGELOG (its section takes
  the new number), the docs if they name it — and a new merge is made; the dead one is meant
  never to have existed. On GitHub it shows as a discrepancy: the last tag exists, its release
  doesn't. Seeing that discrepancy without the user having said so: ask them whether that
  version is dead, before merging.
- **The CHANGELOG's section is the version's number** (`## v1.4.4 (beta)`), released or not — never
  `[Unreleased]`. Whether a version is released is on GitHub (its releases), not in the files.
- **Build numbers** (`1.4.4b1`, `b2`…) only in Claude's working copy and in discussions with
  the user — never merged: not in `manifest.json`, the CHANGELOG, the docs, a code comment, or a commit
  or PR title.
  Each build is one commit, pushed to the work branch, so that builds can be compared
  (`git diff`) and undone (`git revert`); the build ↔ commit table is given to the user.
- 1.4 releases are betas (GitHub pre-releases).
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

### 3. Attribute units and angles — same tables and rules, lowpass_dt adds the device and state class

| here | card (1.2 only) |
|---|---|
| `attributes.py` `attribute_value`, `_attribute_unit`, `is_circular_attribute`; `sensor.py` `_resolve_auto_circular` | `src/history-series.js` `attributeValue`, `_seriesValue`, `attributeUnitOf`, `isDirectionAttribute`; `src/card-datasets.js` `_circularPeriod` |

Common rule: the unit is the one the value gives (`12.5 °C`, comma decimal accepted), else
the weather table (`*_unit` attributes; `wind_bearing` in `°`, which Home Assistant gives
without a unit), the domain table (with factors: brightness × 100/255, volume_level × 100),
Home Assistant's temperature unit for the temperature attributes, else the `X_unit`
attribute of the longest X the name contains. An `X_unit` attribute has no unit.
Both accept the attribute written after the entity_id (`climate.salon.current_temperature`:
an entity_id has a single dot), and a `unit:` option that always wins.

Sub-attributes: the attribute is a path, read level by level — the key in a dictionary, the
index in a list (`forecast.0.temperature`); an attribute whose own name holds a dot is taken
as is. Its unit, classes and whether it is a direction come from its last name that is not
an index (`temperature`); its label shows each level, joined by ` › `. (here `attributes.py`
`attribute_at`, `_leaf_name`, `attribute_label`; the card `attributeAt`, `_leafName`,
`attributeLabel`.)

Automatic `circular` for an attribute: `measurement_angle`, or a unit of `°` only when its
name is a direction (`bearing|direction|azimuth|heading|(^|_)yaw|wind_?dir`): the sun's
`elevation` is not circular. An entity's own state keeps rule 1.

Difference: the device_class / state_class derivation exists only here (the card does not
need them).

Introduced in lowpass_dt v1.4.1 / card v1.2.13.
