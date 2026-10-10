"""patterns: creation by entity_id pattern, cleanup, recursion guard (loader.py)."""
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import setup_lowpass

ATTRS = {"unit_of_measurement": "W", "state_class": "measurement"}


def register_source(hass, object_id):
    """A source with a unique_id (most integrations): it is in the entity registry."""
    return er.async_get(hass).async_get_or_create("sensor", "demo", object_id, suggested_object_id=object_id).entity_id


async def start(hass):
    """The test core is already running: replay the startup scan."""
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()


def lowpass_ids(hass):
    return sorted(e.entity_id for e in er.async_get(hass).entities.values() if e.platform == "lowpass_dt")


async def test_startup_scan_creates_matching_entities(hass: HomeAssistant):
    for n in ("power_a", "power_b", "temp_c"):
        eid = register_source(hass, n)
        hass.states.async_set(eid, "10", ATTRS)
    await setup_lowpass(hass, patterns=[{"match": "sensor.power_*", "tau": 30}])
    await start(hass)
    assert hass.states.get("sensor.lp_power_a") is not None
    assert hass.states.get("sensor.lp_power_b") is not None
    assert hass.states.get("sensor.lp_temp_c") is None


async def test_dynamic_creation_when_a_source_appears(hass: HomeAssistant):
    await setup_lowpass(hass, patterns=[{"match": "sensor.power_*"}])
    await start(hass)
    hass.states.async_set("sensor.power_new", "5", ATTRS)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.lp_power_new") is not None


async def test_broad_pattern_filters_its_own_new_entities(hass: HomeAssistant, monkeypatch):
    """BUG. A new filtered entity is first registered under an id derived from its name
    (sensor.power_a_filtered), which the recursion guard does not know (it only knows the
    final sensor.lp_power_a). A pattern such as sensor.* then filters it again, and the
    filtered copy again, and so on: lp_lp_lp_…, until the limit of 100 entities, or a
    RecursionError in the name updates (each level rewrites the next one synchronously).
    The limit is lowered to 3 here to keep the test quick and clean."""
    import custom_components.lowpass_dt.loader as loader

    monkeypatch.setattr(loader, "MAX_PATTERN_ENTITIES", 3)
    eid = register_source(hass, "power_a")
    hass.states.async_set(eid, "10", ATTRS)
    await setup_lowpass(hass, patterns=[{"match": "sensor.*"}])
    await start(hass)
    for t in range(5):
        hass.states.async_set(eid, str(11 + t), ATTRS)
        await hass.async_block_till_done()
    ids = lowpass_ids(hass)
    assert "sensor.lp_power_a" in ids
    assert any(i.startswith("sensor.lp_lp_") for i in ids), ids


async def test_broad_pattern_is_safe_once_entities_are_registered(hass: HomeAssistant):
    """After a restart, the filtered entities are in the registry under their final id: the
    guard (platform == lowpass_dt) then works."""
    eid = register_source(hass, "power_a")
    er.async_get(hass).async_get_or_create("sensor", "lowpass_dt", "lowpass_dt::lp_power_a", suggested_object_id="lp_power_a")
    hass.states.async_set(eid, "10", ATTRS)
    await setup_lowpass(hass, patterns=[{"match": "sensor.*"}])
    await start(hass)
    for t in range(3):
        hass.states.async_set(eid, str(11 + t), ATTRS)
        await hass.async_block_till_done()
    assert lowpass_ids(hass) == ["sensor.lp_power_a"]


async def test_removed_explicit_entry_is_cleaned_up(hass: HomeAssistant):
    er.async_get(hass).async_get_or_create("sensor", "lowpass_dt", "lowpass_dt::lp_old", suggested_object_id="lp_old")
    hass.states.async_set("sensor.src", "1", ATTRS)
    await setup_lowpass(hass, sensors=[{"source": "sensor.src"}])
    # the orphan is not attached to the config entry yet in this test: attach it
    reg = er.async_get(hass)
    entry = hass.config_entries.async_entries("lowpass_dt")[0]
    reg.async_update_entity("sensor.lp_old", config_entry_id=entry.entry_id)
    await start(hass)
    assert "sensor.lp_old" not in lowpass_ids(hass)
    assert "sensor.lp_src" in lowpass_ids(hass)


async def test_source_without_unique_id_is_created_then_cleaned_up(hass: HomeAssistant):
    """BUG. A source not in the entity registry (a YAML sensor without unique_id) is
    filtered by the dynamic path (it looks at states), but the startup scan only looks at
    the registry: at each start, the cleanup removes its filtered entity from the registry,
    and the dynamic path creates it again at its next state change."""
    await setup_lowpass(hass, patterns=[{"match": "sensor.power_*"}])
    hass.states.async_set("sensor.power_yaml", "5", ATTRS)        # no registry entry
    await hass.async_block_till_done()
    assert "sensor.lp_power_yaml" in lowpass_ids(hass)
    await start(hass)                                              # as at the next start
    assert "sensor.lp_power_yaml" not in lowpass_ids(hass)


async def test_non_numeric_source_gets_a_filtered_entity(hass: HomeAssistant):
    """A pattern also matches date or text sensors; a date is parsed as its year with
    the rest as a unit ("2026-10-10" → 2026, unit "-10-10")."""
    eid = register_source(hass, "power_last_reset")
    hass.states.async_set(eid, "2026-10-10", {})
    await setup_lowpass(hass, patterns=[{"match": "sensor.power_*"}])
    await start(hass)
    st = hass.states.get("sensor.lp_power_last_reset")
    assert st is not None
    assert st.state == "2026.0"
    assert st.attributes.get("unit_of_measurement") == "-10-10"


async def test_reload_after_start_skips_the_startup_scan(hass: HomeAssistant):
    """After a reload of the entry (Home Assistant already running), 'homeassistant_started'
    never fires again: matching sources get their filtered entity back only at their next
    state change, and the cleanup does not run."""
    eid = register_source(hass, "power_a")
    hass.states.async_set(eid, "10", ATTRS)
    await setup_lowpass(hass, patterns=[{"match": "sensor.power_*"}])
    await start(hass)
    assert hass.states.get("sensor.lp_power_a").state != "unavailable"

    entry = hass.config_entries.async_entries("lowpass_dt")[0]
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    st = hass.states.get("sensor.lp_power_a")
    assert st is None or st.state == "unavailable"

    hass.states.async_set(eid, "11", ATTRS)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.lp_power_a").state not in ("unavailable", None)
