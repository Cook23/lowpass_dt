from __future__ import annotations

from homeassistant import config_entries

from .const import DOMAIN


class LowpassDtConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    # ------------------------------------------------------------
    # YAML import handler
    # ------------------------------------------------------------
    async def async_step_import(self, data):
        """Handle YAML import."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title="Low-pass dt (YAML)", data=data or {})