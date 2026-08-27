"""Select platform for Sub-Zero BLE ice maker mode."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SubZeroConfigEntry
from .const import DOMAIN, ICE_MAKER_OPTIONS
from .coordinator import SubZeroDataUpdateCoordinator, ice_maker_mode

ICE_MAKER = SelectEntityDescription(
    key="ice_maker_mode",
    name="Ice Maker",
    icon="mdi:snowflake",
    options=list(ICE_MAKER_OPTIONS),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SubZeroConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Sub-Zero select entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        [SubZeroIceMakerSelect(coordinator, entry.data["address"])]
    )


class SubZeroIceMakerSelect(
    CoordinatorEntity[SubZeroDataUpdateCoordinator], SelectEntity
):
    """Off / Normal / Night Ice / Max Ice control."""

    entity_description = ICE_MAKER

    def __init__(self, coordinator: SubZeroDataUpdateCoordinator, address: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{address}_{ICE_MAKER.key}"
        self._attr_options = list(ICE_MAKER_OPTIONS)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            name="Sub-Zero Refrigerator",
            manufacturer="Sub-Zero",
        )

    @property
    def current_option(self) -> str | None:
        """Return the derived ice maker mode."""
        if self.coordinator.data is None:
            return None
        return ice_maker_mode(self.coordinator.data)

    async def async_select_option(self, option: str) -> None:
        """Write the three ice-maker booleans for this mode."""
        await self.coordinator.async_set_ice_maker_mode(option)
