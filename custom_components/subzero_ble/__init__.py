"""The Sub-Zero BLE integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

_PLATFORMS: list[Platform] = []

type SubZeroBLEConfigEntry = ConfigEntry


async def async_setup_entry(hass: HomeAssistant, entry: SubZeroBLEConfigEntry) -> bool:
    """Set up Sub-Zero BLE from a config entry."""
    if _PLATFORMS:
        await hass.config_entries.async_forward_entry_setups(entry, _PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SubZeroBLEConfigEntry) -> bool:
    """Unload a config entry."""
    if not _PLATFORMS:
        return True
    return await hass.config_entries.async_unload_platforms(entry, _PLATFORMS)
