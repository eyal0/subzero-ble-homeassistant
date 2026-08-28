"""Button platform for Sub-Zero BLE."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SubZeroConfigEntry
from .coordinator import SubZeroDataUpdateCoordinator
from .entity import SubZeroEntity

PARALLEL_UPDATES = 1

DISPLAY_PIN = ButtonEntityDescription(
    key="display_pin",
    name="Show PIN",
    icon="mdi:dialpad",
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


class SubZeroDisplayPinButton(SubZeroEntity, ButtonEntity):
    """Button that asks the appliance to show its PIN on the display."""

    entity_description = DISPLAY_PIN

    def __init__(self, coordinator: SubZeroDataUpdateCoordinator, address: str) -> None:
        super().__init__(coordinator, address, DISPLAY_PIN.key)

    async def async_press(self) -> None:
        """Ask the appliance to show its PIN on the display."""
        await self.coordinator.async_display_pin()
