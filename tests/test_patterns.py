"""patterns: creation by entity_id pattern, cleanup, recursion guard (loader.py)."""
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant
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


async def test_broad_pattern_does_not_filter_its_own_entities(hass: HomeAssistant):
    """A pattern such as sensor.* must not filter the filtered entities (it used to, in a
    chain lp_lp_lp_…, while a new entity was first registered under another id)."""
    eid = register_source(hass, "power_a")
    hass.states.async_set(eid, "10", ATTRS)
    await setup_lowpass(hass, patterns=[{"match": "sensor.*"}])
    await start(hass)
    for t in range(5):
        hass.states.async_set(eid, str(11 + t), ATTRS)
        await hass.async_block_till_done()
    assert lowpass_ids(hass) == ["sensor.lp_power_a"]


async def test_broad_pattern_skips_a_renamed_filtered_entity(hass: HomeAssistant):
    """The guard also knows a filtered entity the user renamed (by the registry)."""
    eid = register_source(hass, "power_a")
    hass.states.async_set(eid, "10", ATTRS)
    await setup_lowpass(hass, patterns=[{"match": "sensor.*"}])
    await start(hass)
    er.async_get(hass).async_update_entity("sensor.lp_power_a", new_entity_id="sensor.power_smooth")
    await hass.async_block_till_done()
    for t in range(3):
        hass.states.async_set(eid, str(20 + t), ATTRS)
        await hass.async_block_till_done()
    assert lowpass_ids(hass) == ["sensor.power_smooth"]


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
    hass.set_state(CoreState.starting)
    await setup_lowpass(hass, sensors=[{"source": "sensor.src"}])
    # the orphan is not attached to the config entry yet in this test: attach it
    reg = er.async_get(hass)
    entry = hass.config_entries.async_entries("lowpass_dt")[0]
    reg.async_update_entity("sensor.lp_old", config_entry_id=entry.entry_id)
    hass.set_state(CoreState.running)
    await start(hass)
    assert "sensor.lp_old" not in lowpass_ids(hass)
    assert "sensor.lp_src" in lowpass_ids(hass)


async def test_source_without_unique_id_is_kept_at_start(hass: HomeAssistant):
    """A source not in the entity registry (a YAML sensor without unique_id) is filtered,
    and its filtered entity survives the startup scan (it used to be removed from the
    registry at each start, and created again at the source's next state change)."""
    await setup_lowpass(hass, patterns=[{"match": "sensor.power_*"}])
    hass.states.async_set("sensor.power_yaml", "5", ATTRS)        # no registry entry
    await hass.async_block_till_done()
    assert "sensor.lp_power_yaml" in lowpass_ids(hass)
    await start(hass)                                              # as at the next start
    assert "sensor.lp_power_yaml" in lowpass_ids(hass)


async def test_startup_scan_finds_source_without_unique_id(hass: HomeAssistant):
    hass.states.async_set("sensor.power_yaml", "5", ATTRS)
    hass.set_state(CoreState.starting)
    await setup_lowpass(hass, patterns=[{"match": "sensor.power_*"}])
    hass.set_state(CoreState.running)
    await start(hass)
    assert hass.states.get("sensor.lp_power_yaml") is not None


async def test_non_numeric_source_is_not_filtered(hass: HomeAssistant):
    """A pattern also matches date or text sensors: they get no filtered entity."""
    eid = register_source(hass, "power_last_reset")
    hass.states.async_set(eid, "2026-10-10", {})
    hass.states.async_set("sensor.power_mode", "eco", {})
    await setup_lowpass(hass, patterns=[{"match": "sensor.power_*"}])
    await start(hass)
    hass.states.async_set("sensor.power_mode", "boost", {})
    await hass.async_block_till_done()
    assert lowpass_ids(hass) == []


async def test_reload_after_start_scans_again(hass: HomeAssistant):
    """After a reload of the entry (Home Assistant already running), the scan runs at once:
    'homeassistant_started' will not fire again."""
    eid = register_source(hass, "power_a")
    hass.states.async_set(eid, "10", ATTRS)
    await setup_lowpass(hass, patterns=[{"match": "sensor.power_*"}])
    await start(hass)
    entry = hass.config_entries.async_entries("lowpass_dt")[0]
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    st = hass.states.get("sensor.lp_power_a")
    assert st is not None and st.state not in ("unavailable", "unknown")


async def test_setup_while_running_scans_at_once(hass: HomeAssistant):
    eid = register_source(hass, "power_a")
    hass.states.async_set(eid, "10", ATTRS)
    await setup_lowpass(hass, patterns=[{"match": "sensor.power_*"}])
    assert hass.states.get("sensor.lp_power_a") is not None


async def test_guard_holds_whatever_entity_id_home_assistant_picks(hass: HomeAssistant, monkeypatch):
    """Even if Home Assistant ignored the entity_id the integration sets, and derived it from
    the friendly name instead (sensor.power_a_filtered, without the lp_ prefix), a broad
    pattern must not filter the filtered entity: the guard knows it by the entity registry
    (platform lowpass_dt), not by its entity_id."""
    from custom_components.lowpass_dt.sensor import LowpassDtSensor

    orig_init = LowpassDtSensor.__init__

    def init_without_entity_id(self, *args, **kwargs):
        orig_init(self, *args, **kwargs)
        self.entity_id = None

    monkeypatch.setattr(LowpassDtSensor, "__init__", init_without_entity_id)
    eid = register_source(hass, "power_a")
    hass.states.async_set(eid, "10", {**ATTRS, "friendly_name": "Power A"})
    await setup_lowpass(hass, patterns=[{"match": "sensor.*"}])
    await start(hass)
    for t in range(5):
        hass.states.async_set(eid, str(11 + t), {**ATTRS, "friendly_name": "Power A"})
        await hass.async_block_till_done()
    assert lowpass_ids(hass) == ["sensor.power_a_filtered"]


async def test_normal_operation_logs_no_warning(hass: HomeAssistant, caplog):
    """New entities, a broad pattern, restarts and publishing: nothing at WARNING level."""
    import logging

    caplog.set_level(logging.INFO)
    eid = register_source(hass, "power_a")
    hass.states.async_set(eid, "10", ATTRS)
    hass.states.async_set("sensor.src", "10", ATTRS)
    await setup_lowpass(hass, sensors=[{"source": "sensor.src"}], patterns=[{"match": "sensor.*"}])
    await start(hass)
    for t in range(5):
        hass.states.async_set(eid, str(11 + t), ATTRS)
        hass.states.async_set("sensor.src", str(11 + t), ATTRS)
        await hass.async_block_till_done()
    entry = hass.config_entries.async_entries("lowpass_dt")[0]
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    warnings = [r.getMessage() for r in caplog.records if r.name.startswith("custom_components.lowpass_dt") and r.levelno >= logging.WARNING]
    assert warnings == []


async def test_explicit_chaining_allowed_a_pattern_never_chains(hass: HomeAssistant):
    """A filtered entity named in sensors: is filtered again (wanted); a pattern matching
    it does not filter it (most likely a mistake)."""
    eid = register_source(hass, "power_a")
    hass.states.async_set(eid, "10", ATTRS)
    await setup_lowpass(hass, sensors=[{"source": eid}, {"source": "sensor.lp_power_a"}], patterns=[{"match": "sensor.*"}])
    await start(hass)
    for t in range(3):
        hass.states.async_set(eid, str(11 + t), ATTRS)
        await hass.async_block_till_done()
    assert lowpass_ids(hass) == ["sensor.lp_lp_power_a", "sensor.lp_power_a"]
    assert hass.states.get("sensor.lp_lp_power_a").attributes["source"] == "sensor.lp_power_a"

