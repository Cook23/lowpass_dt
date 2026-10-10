"""entity_id of the filtered entities: never derived from the friendly name, never changed
behind the user's back. Every registry event of the integration is recorded and checked,
through the asynchronous paths where Home Assistant could pick another id: source not yet
known, friendly name arriving later, pattern creation at startup and at runtime, reload,
and the UI's "Recreate entity IDs"."""
import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import setup_lowpass

# "Recreate entity IDs" regenerates an id from the registry entry's suggested_object_id
# and object_id_base, which older Home Assistant versions do not store
_RECREATE = hasattr(er.RegistryEntry, "suggested_object_id") and hasattr(er.EntityRegistry, "async_regenerate_entity_id")
needs_recreate = pytest.mark.skipif(not _RECREATE, reason="no 'Recreate entity IDs' in this Home Assistant version")

ATTRS = {"unit_of_measurement": "W", "state_class": "measurement"}


def watch_registry(hass):
    """Records (action, entity_id, old_entity_id) for every lowpass_dt registry event."""
    events = []

    def _listener(event):
        eid = event.data["entity_id"]
        entry = er.async_get(hass).async_get(eid)
        if entry is not None and entry.platform != "lowpass_dt":
            return
        events.append((event.data["action"], eid, event.data.get("old_entity_id")))

    hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, _listener)
    return events


def created(events):
    return [e[1] for e in events if e[0] == "create"]


def renamed(events):
    return [(e[2], e[1]) for e in events if e[0] == "update" and e[2]]


def lowpass_ids(hass):
    return sorted(e.entity_id for e in er.async_get(hass).entities.values() if e.platform == "lowpass_dt")


async def settle(hass, rounds=5):
    for _ in range(rounds):
        await hass.async_block_till_done()


async def test_explicit_entity_with_friendly_name(hass: HomeAssistant):
    events = watch_registry(hass)
    hass.states.async_set("sensor.src", "1", {**ATTRS, "friendly_name": "Kitchen power"})
    await setup_lowpass(hass, sensors=[{"source": "sensor.src"}])
    for v in range(2, 6):
        hass.states.async_set("sensor.src", str(v), {**ATTRS, "friendly_name": "Kitchen power"})
        await settle(hass)
    assert created(events) == ["sensor.lp_src"]
    assert renamed(events) == []
    assert hass.states.get("sensor.lp_src").attributes["friendly_name"] == "Kitchen power (Filtered)"
    assert hass.states.get("sensor.kitchen_power_filtered") is None


async def test_source_unknown_at_setup_friendly_name_arrives_later(hass: HomeAssistant):
    """The source does not exist yet: the name is first derived from its object_id, and
    the real friendly name arrives asynchronously with its first state."""
    events = watch_registry(hass)
    await setup_lowpass(hass, sensors=[{"source": "sensor.src"}])
    hass.states.async_set("sensor.src", "unavailable", {"friendly_name": "Garage power"})
    await settle(hass)
    hass.states.async_set("sensor.src", "3", {**ATTRS, "friendly_name": "Garage power"})
    await settle(hass)
    hass.states.async_set("sensor.src", "4", {**ATTRS, "friendly_name": "Garage power renamed"})
    await settle(hass)
    assert created(events) == ["sensor.lp_src"]
    assert renamed(events) == []
    assert lowpass_ids(hass) == ["sensor.lp_src"]


async def test_pattern_entities_startup_and_runtime(hass: HomeAssistant):
    events = watch_registry(hass)
    reg = er.async_get(hass)
    reg.async_get_or_create("sensor", "demo", "a", suggested_object_id="power_a")
    hass.states.async_set("sensor.power_a", "1", {**ATTRS, "friendly_name": "Power A"})
    await setup_lowpass(hass, patterns=[{"match": "sensor.power_*"}])
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await settle(hass)
    hass.states.async_set("sensor.power_b", "1", {**ATTRS, "friendly_name": "Power B"})
    await settle(hass)
    for v in range(2, 5):
        hass.states.async_set("sensor.power_a", str(v), {**ATTRS, "friendly_name": "Power A"})
        hass.states.async_set("sensor.power_b", str(v), {**ATTRS, "friendly_name": "Power B"})
        await settle(hass)
    assert sorted(created(events)) == ["sensor.lp_power_a", "sensor.lp_power_b"]
    assert renamed(events) == []


async def test_name_option(hass: HomeAssistant):
    events = watch_registry(hass)
    hass.states.async_set("sensor.src", "1", {**ATTRS, "friendly_name": "Kitchen power"})
    await setup_lowpass(hass, sensors=[{"source": "sensor.src", "name": "Smooth power"}])
    await settle(hass)
    assert created(events) == ["sensor.smooth_power"]
    assert renamed(events) == []


async def test_reload_keeps_entity_id(hass: HomeAssistant):
    hass.states.async_set("sensor.src", "1", {**ATTRS, "friendly_name": "Kitchen power"})
    await setup_lowpass(hass, sensors=[{"source": "sensor.src"}])
    events = watch_registry(hass)
    entry = hass.config_entries.async_entries("lowpass_dt")[0]
    assert await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)
    assert created(events) == []
    assert renamed(events) == []
    assert lowpass_ids(hass) == ["sensor.lp_src"]


@needs_recreate
async def test_recreate_entity_ids_in_ui_gives_lp_id(hass: HomeAssistant):
    """The UI's "Recreate entity IDs" regenerates the id from the registry entry: the
    integration's id is stored there as suggested_object_id, which wins over the name."""
    hass.states.async_set("sensor.src", "1", {**ATTRS, "friendly_name": "Kitchen power"})
    await setup_lowpass(hass, sensors=[{"source": "sensor.src"}])
    reg = er.async_get(hass)
    entry = reg.async_get("sensor.lp_src")
    assert entry.suggested_object_id == "lp_src"
    assert reg.async_regenerate_entity_id(entry) == "sensor.lp_src"


@needs_recreate
async def test_entry_registered_by_1_3_17_is_kept_and_fixed_for_recreate(hass: HomeAssistant):
    """An entry created by 1.3.17: renamed to lp_src, with no suggested_object_id and the
    name as object_id_base. Its id is kept, and from the first start it stores lp_src as
    suggested_object_id, so that "Recreate entity IDs" no longer gives src_filtered."""
    reg = er.async_get(hass)
    old = reg.async_get_or_create("sensor", "lowpass_dt", "lowpass_dt::lp_src", original_name="Kitchen power (Filtered)", object_id_base="Kitchen power (Filtered)")
    assert old.entity_id == "sensor.kitchen_power_filtered"         # what 1.3.17 got first
    reg.async_update_entity(old.entity_id, new_entity_id="sensor.lp_src")  # then its rename
    assert reg.async_regenerate_entity_id(reg.async_get("sensor.lp_src")) == "sensor.kitchen_power_filtered"
    events = watch_registry(hass)
    hass.states.async_set("sensor.src", "1", {**ATTRS, "friendly_name": "Kitchen power"})
    await setup_lowpass(hass, sensors=[{"source": "sensor.src"}])
    await settle(hass)
    assert created(events) == [] and renamed(events) == []
    entry = reg.async_get("sensor.lp_src")
    assert entry.suggested_object_id == "lp_src"
    assert reg.async_regenerate_entity_id(entry) == "sensor.lp_src"
