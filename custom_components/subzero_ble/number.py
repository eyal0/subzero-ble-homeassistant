"""Number platform for Sub-Zero BLE setpoints."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SubZeroConfigEntry
from .const import DOMAIN
from .coordinator import SubZeroData, SubZeroDataUpdateCoordinator

# Wire values match the appliance BLE integers (Fahrenheit). Typical Sub-Zero
# fridge range is 34–45°F; freezer is about -5–5°F. Bounds are slightly wider
# so a reported value outside the usual band still displays.
FRIDGE_TEMP_MIN = 30
FRIDGE_TEMP_MAX = 48
FREEZER_TEMP_MIN = -10
FREEZER_TEMP_MAX = 10


@dataclass(frozen=True, kw_only=True)
class SubZeroNumberEntityDescription(NumberEntityDescription):
    """Describes a writable Sub-Zero setpoint."""

    property_key: str
    value_fn: Callable[[SubZeroData], float | None]


NUMBER_DESCRIPTIONS: tuple[SubZeroNumberEntityDescription, ...] = (
    SubZeroNumberEntityDescription(
        key="fridge_temperature",
        name="Refrigerator Temperature",
        icon="mdi:thermometer",
        native_min_value=FRIDGE_TEMP_MIN,
        native_max_value=FRIDGE_TEMP_MAX,
        native_step=1,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        device_class=NumberDeviceClass.TEMPERATURE,
        mode=NumberMode.BOX,
        property_key="ref_set_temp",
        value_fn=lambda data: data.fridge_temp,
    ),
    SubZeroNumberEntityDescription(
        key="freezer_temperature",
        name="Freezer Temperature",
        icon="mdi:snowflake-thermometer",
        native_min_value=FREEZER_TEMP_MIN,
        native_max_value=FREEZER_TEMP_MAX,
        native_step=1,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        device_class=NumberDeviceClass.TEMPERATURE,
        mode=NumberMode.BOX,
        property_key="frz_set_temp",
        value_fn=lambda data: data.freezer_temp,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SubZeroConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Sub-Zero number entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        SubZeroNumberEntity(coordinator, desc, entry.data["address"])
        for desc in NUMBER_DESCRIPTIONS
    )


class SubZeroNumberEntity(
    CoordinatorEntity[SubZeroDataUpdateCoordinator], NumberEntity
):
    """Writable fridge or freezer setpoint."""

    entity_description: SubZeroNumberEntityDescription

    def __init__(
        self,
        coordinator: SubZeroDataUpdateCoordinator,
        description: SubZeroNumberEntityDescription,
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
    def native_value(self) -> float | None:
        """Return the current setpoint."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    async def async_set_native_value(self, value: float) -> None:
        """Write the setpoint to the appliance."""
        await self.coordinator.async_set_temperature(
            self.entity_description.property_key, value
        )
