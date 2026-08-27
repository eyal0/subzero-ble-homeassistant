"""Switch platform for Sub-Zero BLE toggles."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SubZeroConfigEntry
from .const import DOMAIN
from .coordinator import SubZeroData, SubZeroDataUpdateCoordinator, field_bool


@dataclass(frozen=True, kw_only=True)
class SubZeroSwitchEntityDescription(SwitchEntityDescription):
    """Describes a writable Sub-Zero boolean."""

    property_key: str
    is_on_fn: Callable[[SubZeroData], bool | None]
    on_value: object = True
    off_value: object = False


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


class SubZeroSwitchEntity(
    CoordinatorEntity[SubZeroDataUpdateCoordinator], SwitchEntity
):
    """Writable boolean on the encrypted D5 channel."""

    entity_description: SubZeroSwitchEntityDescription

    def __init__(
        self,
        coordinator: SubZeroDataUpdateCoordinator,
        description: SubZeroSwitchEntityDescription,
        address: str,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{address}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            name="Sub-Zero Refrigerator",
            manufacturer="Sub-Zero",
        )

    @property
    def is_on(self) -> bool | None:
        """Return True if the switch is on."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.is_on_fn(self.coordinator.data)

    async def async_turn_on(self, **kwargs: object) -> None:
        """Turn the appliance feature on."""
        await self.coordinator.async_set_value(
            self.entity_description.property_key,
            self.entity_description.on_value,
        )

    async def async_turn_off(self, **kwargs: object) -> None:
        """Turn the appliance feature off."""
        await self.coordinator.async_set_value(
            self.entity_description.property_key,
            self.entity_description.off_value,
        )
