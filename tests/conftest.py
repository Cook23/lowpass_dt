"""Shared fixtures: a real Home Assistant core (pytest-homeassistant-custom-component)."""
from pathlib import Path

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"

# The plugin ships its own `custom_components` package, which shadows this repository's:
# add ours to its search path so that Home Assistant finds lowpass_dt.
import custom_components  # noqa: E402

_OURS = str(Path(__file__).resolve().parent.parent / "custom_components")
if _OURS not in custom_components.__path__:
    custom_components.__path__.append(_OURS)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/lowpass_dt in every test."""
    yield


async def setup_lowpass(hass, sensors=None, patterns=None):
    """Set up lowpass_dt from a YAML-like dict and wait until it has settled."""
    from homeassistant.setup import async_setup_component

    conf = {}
    if sensors is not None:
        conf["sensors"] = sensors
    if patterns is not None:
        conf["patterns"] = patterns
    assert await async_setup_component(hass, "lowpass_dt", {"lowpass_dt": conf})
    await hass.async_block_till_done()
