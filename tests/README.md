# Tests

Run against a real Home Assistant core (pytest-homeassistant-custom-component):

```
python3 -m venv venv
venv/bin/pip install -r requirements_test.txt
venv/bin/pytest
```

| File | What it covers |
|---|---|
| `test_config.py` | YAML options: defaults, validation, `circular` values |
| `test_core.py` | Filter math: ZOH, circular mean, deadband |
| `test_parsing.py` | Source state parsing, output decimals |
| `test_entity.py` | The entity in Home Assistant: publishing, silence, marker, circular, total_increasing, restore |
| `test_patterns.py` | `patterns`: startup scan, dynamic creation, cleanup, recursion guard, reload |

Tests whose docstring starts with **BUG** or says **Known weakness / inconsistency** assert
the current, wrong behaviour, so that the suite passes today and documents it. Once a fix
lands, such a test fails: invert it into a test of the fixed behaviour.

The silence tests use the `injector_in_loop` fixture: without it they are flaky, because the
injector's timer callbacks run in a worker thread (`test_injection_runs_in_a_worker_thread`).
