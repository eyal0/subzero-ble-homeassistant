"""Button platform for Sub-Zero BLE."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SubZeroConfigEntry
from .const import DOMAIN
from .coordinator import SubZeroDataUpdateCoordinator

DISPLAY_PIN = ButtonEntityDescription(
    key="display_pin",
    name="Start pairing",
    icon="mdi:key-plus",
    entity_category=EntityCategory.CONFIG,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SubZeroConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Sub-Zero buttons."""
    coordinator = entry.runtime_data
    async_add_entities(
        [SubZeroDisplayPinButton(coordinator, entry.data["address"])]
    )


class SubZeroDisplayPinButton(
    CoordinatorEntity[SubZeroDataUpdateCoordinator], ButtonEntity
):
    """Button that asks the appliance to show its PIN on the display."""

    entity_description = DISPLAY_PIN

    def __init__(self, coordinator: SubZeroDataUpdateCoordinator, address: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{address}_{DISPLAY_PIN.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            name="Sub-Zero Refrigerator",
            manufacturer="Sub-Zero",
        )

    async def async_press(self) -> None:
        """Send display_pin to the appliance."""
        await self.coordinator.async_display_pin()
