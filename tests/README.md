# Tests

Run against a real Home Assistant core (pytest-homeassistant-custom-component):

```
python3.14 -m venv venv
venv/bin/pip install -r requirements_test.txt
venv/bin/pytest
```

`requirements_test.txt` pins Home Assistant 2026.10, which needs Python 3.14. With Python
3.13, `pytest-homeassistant-custom-component==0.13.316` gives Home Assistant 2026.2. The
suite is run on both.

| File | What it covers |
|---|---|
| `test_config.py` | YAML options: defaults, validation, `circular` values |
| `test_core.py` | Filter math: ZOH, circular mean, deadband |
| `test_parsing.py` | Source state parsing, output decimals |
| `test_entity.py` | The entity in Home Assistant: publishing, silence, marker, circular, total_increasing, restore |
| `test_patterns.py` | `patterns`: startup scan, dynamic creation, cleanup, recursion guard, reload |
| `test_entity_id.py` | entity_id never derived from the friendly name, on every asynchronous path |
| `test_behavior.py` | the filter as a whole: first order whatever the sampling, few writes, a natural curve redrawn from the stored points (`-s` prints the measures) |

Tests marked `xfail(strict=True)` state a goal the code does not reach yet, with the reason: when
it is reached, they fail as XPASS, and the marker is removed. Tests whose docstring starts with **BUG** or says **Known weakness / inconsistency** assert
the current, wrong behaviour, so that the suite passes today and documents it. Once a fix
lands, such a test fails: invert it into a test of the fixed behaviour.
