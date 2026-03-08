from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN

PLATFORMS = ["sensor"]

# ------------------------------------------------------------
# YAML sync: create/update/remove config entry
# ------------------------------------------------------------
async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Synchronize YAML with the config entry."""
    yaml_data = config.get(DOMAIN)
    entries = hass.config_entries.async_entries(DOMAIN)

    # YAML removed -> remove config entry
    if yaml_data is None:
        if entries:
            hass.async_create_task(
                hass.config_entries.async_remove(entries[0].entry_id)
            )
        return True

    # YAML present -> create or update config entry
    yaml_data = yaml_data or {}

    if entries:
        entry = entries[0]
        if entry.data != yaml_data:
            hass.config_entries.async_update_entry(entry, data=yaml_data)
            hass.async_create_task(
                hass.config_entries.async_reload(entry.entry_id)
            )
    else:
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": "import"},
                data=yaml_data,
            )
        )

    return True

# ------------------------------------------------------------
# Load integration from config entry
# ------------------------------------------------------------
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Load the integration from the config entry."""
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

# ------------------------------------------------------------
# Unload integration
# ------------------------------------------------------------
async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload the integration."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    return unload_ok