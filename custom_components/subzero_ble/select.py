"""Select platform for Sub-Zero BLE mode controls."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SubZeroConfigEntry
from .const import APPLIANCE_MODE_OPTIONS, DOMAIN, ICE_MAKER_OPTIONS
from .coordinator import (
    SubZeroData,
    SubZeroDataUpdateCoordinator,
    appliance_mode,
    ice_maker_mode,
)


@dataclass(frozen=True, kw_only=True)
class SubZeroSelectEntityDescription(SelectEntityDescription):
    """Describes a grouped Sub-Zero mode select."""

    current_fn: Callable[[SubZeroData], str | None]
    set_fn: Callable[[SubZeroDataUpdateCoordinator, str], Awaitable[None]]


SELECT_DESCRIPTIONS: tuple[SubZeroSelectEntityDescription, ...] = (
    SubZeroSelectEntityDescription(
        key="ice_maker_mode",
        name="Ice Maker",
        icon="mdi:snowflake",
        options=list(ICE_MAKER_OPTIONS),
        current_fn=ice_maker_mode,
        set_fn=SubZeroDataUpdateCoordinator.async_set_ice_maker_mode,
    ),
    SubZeroSelectEntityDescription(
        key="appliance_mode",
        name="Mode",
        icon="mdi:tune",
        options=list(APPLIANCE_MODE_OPTIONS),
        current_fn=appliance_mode,
        set_fn=SubZeroDataUpdateCoordinator.async_set_appliance_mode,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SubZeroConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Sub-Zero select entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        SubZeroSelectEntity(coordinator, desc, entry.data["address"])
        for desc in SELECT_DESCRIPTIONS
    )


class SubZeroSelectEntity(
    CoordinatorEntity[SubZeroDataUpdateCoordinator], SelectEntity
):
    """Writable grouped mode (ice maker or appliance)."""

    entity_description: SubZeroSelectEntityDescription

    def __init__(
        self,
        coordinator: SubZeroDataUpdateCoordinator,
        description: SubZeroSelectEntityDescription,
        address: str,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{address}_{description.key}"
        self._attr_options = list(description.options or ())
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            name="Sub-Zero Refrigerator",
            manufacturer="Sub-Zero",
        )

    @property
    def current_option(self) -> str | None:
        """Return the derived mode."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.current_fn(self.coordinator.data)

    async def async_select_option(self, option: str) -> None:
        """Write the grouped boolean flags for this mode."""
        await self.entity_description.set_fn(self.coordinator, option)
