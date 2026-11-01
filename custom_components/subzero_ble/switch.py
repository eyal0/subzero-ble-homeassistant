"""Switch platform for Sub-Zero BLE toggles."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SubZeroConfigEntry
from .const import ACCENT_LIGHT_LEVEL
from .coordinator import (
    SubZeroData,
    SubZeroDataUpdateCoordinator,
    accent_light_is_on,
    accent_light_on_level,
    field_bool,
)
from .entity import SubZeroEntity

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class SubZeroSwitchEntityDescription(SwitchEntityDescription):
    """Describes a writable Sub-Zero boolean."""

    property_key: str
    is_on_fn: Callable[[SubZeroData], bool | None]
    on_value: object = True
    off_value: object = False
    on_value_fn: Callable[[SubZeroData | None], object] | None = None


SWITCH_DESCRIPTIONS: tuple[SubZeroSwitchEntityDescription, ...] = (
    SubZeroSwitchEntityDescription(
        key="air_purifier",
        name="Air Purifier",
        icon="mdi:air-filter",
        property_key="air_filter_on",
        is_on_fn=lambda data: field_bool(data, "air_filter_on"),
    ),
    SubZeroSwitchEntityDescription(
        key="night_mode",
        name="Night Mode",
        icon="mdi:weather-night",
        property_key="night_mode",
        on_value=1,
        off_value=0,
        is_on_fn=lambda data: field_bool(data, "night_mode"),
    ),
    SubZeroSwitchEntityDescription(
        key="accent_light",
        name="Accent Light",
        icon="mdi:lightbulb-outline",
        property_key=ACCENT_LIGHT_LEVEL,
        off_value=0,
        is_on_fn=accent_light_is_on,
        on_value_fn=accent_light_on_level,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SubZeroConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Sub-Zero switches."""
    coordinator = entry.runtime_data
    async_add_entities(
        SubZeroSwitchEntity(coordinator, desc, entry.data["address"])
        for desc in SWITCH_DESCRIPTIONS
    )


class SubZeroSwitchEntity(SubZeroEntity, SwitchEntity):
    """Writable boolean on the encrypted D5 channel."""

    entity_description: SubZeroSwitchEntityDescription

    def __init__(
        self,
        coordinator: SubZeroDataUpdateCoordinator,
        description: SubZeroSwitchEntityDescription,
        address: str,
    ) -> None:
        super().__init__(coordinator, address, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return True if the switch is on."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.is_on_fn(self.coordinator.data)

    async def async_turn_on(self, **kwargs: object) -> None:
        """Turn the appliance feature on."""
        await self._async_write(True)

    async def async_turn_off(self, **kwargs: object) -> None:
        """Turn the appliance feature off."""
        await self._async_write(False)

    async def _async_write(self, turn_on: bool) -> None:
        """Write this switch's on or off value on D5."""
        description = self.entity_description
        if turn_on and description.on_value_fn is not None:
            value = description.on_value_fn(self.coordinator.data)
        else:
            value = description.on_value if turn_on else description.off_value
        await self.coordinator.async_set_value(description.property_key, value)
