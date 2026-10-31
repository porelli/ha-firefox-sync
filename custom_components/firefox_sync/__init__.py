"""The Firefox Sync integration: the firefox-sync admin UI's metrics, and the health of the services."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr

from .api import SERVICES
from .const import CONF_SYNC_URL, DOMAIN
from .coordinator import FirefoxSyncConfigEntry, FirefoxSyncCoordinator

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: FirefoxSyncConfigEntry) -> bool:
    """Set up one entry: an admin UI, the services' URLs, or both."""
    coordinator = FirefoxSyncCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    @callback
    def _sw_version() -> None:
        """The device's software version: the Sync server's (its /__version__)."""
        if coordinator.data is None:
            return
        version = coordinator.data.versions.get(SERVICES[CONF_SYNC_URL].component)
        registry = dr.async_get(hass)
        # (the entry's one device; by entry, which every Home Assistant version has)
        for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
            if (DOMAIN, entry.entry_id) in device.identifiers and version and device.sw_version != version:
                registry.async_update_device(device.id, sw_version=version)

    entry.async_on_unload(coordinator.async_add_listener(_sw_version))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: FirefoxSyncConfigEntry) -> bool:
    """Unload one entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
