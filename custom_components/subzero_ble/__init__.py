"""Sub-Zero BLE Integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import VERSION
from .coordinator import SubZeroDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
]

type SubZeroConfigEntry = ConfigEntry[SubZeroDataUpdateCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: SubZeroConfigEntry) -> bool:
    """Set up Sub-Zero BLE from a config entry."""
    _LOGGER.info(
        "Setting up Sub-Zero BLE %s for %s (%s)",
        VERSION,
        entry.title,
        entry.unique_id or entry.data.get("address"),
    )
    coordinator = SubZeroDataUpdateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: SubZeroConfigEntry
) -> None:
    """Reload when the pairing PIN is changed."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: SubZeroConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading Sub-Zero BLE %s", entry.title)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    await entry.runtime_data.async_disconnect()
    return unload_ok
