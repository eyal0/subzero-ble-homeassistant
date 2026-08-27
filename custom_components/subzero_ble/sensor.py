"""Sensor platform for Sub-Zero BLE."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SubZeroConfigEntry
from .const import DOMAIN
from .coordinator import SubZeroData, SubZeroDataUpdateCoordinator


@dataclass(frozen=True, kw_only=True)
class SubZeroSensorEntityDescription(SensorEntityDescription):
    """Describes Sub-Zero sensor entity."""
    value_fn: Callable[[SubZeroData], float | int | None]


SENSOR_DESCRIPTIONS: tuple[SubZeroSensorEntityDescription, ...] = (
    SubZeroSensorEntityDescription(
        key="fridge_temperature",
        name="Refrigerator Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.fridge_temp,
    ),
    SubZeroSensorEntityDescription(
        key="freezer_temperature",
        name="Freezer Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.freezer_temp,
    ),
    SubZeroSensorEntityDescription(
        key="water_filter_life",
        name="Water Filter Life Remaining",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.water_filter_life,
    ),
    SubZeroSensorEntityDescription(
        key="air_filter_life",
        name="Air Filter Life Remaining",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.air_filter_life,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SubZeroConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Sub-Zero sensors from config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        SubZeroSensorEntity(coordinator, desc, entry.data["address"])
        for desc in SENSOR_DESCRIPTIONS
    )


class SubZeroSensorEntity(CoordinatorEntity[SubZeroDataUpdateCoordinator], SensorEntity):
    """Representation of a Sub-Zero sensor."""

    entity_description: SubZeroSensorEntityDescription

    def __init__(
        self,
        coordinator: SubZeroDataUpdateCoordinator,
        description: SubZeroSensorEntityDescription,
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
    def native_value(self) -> float | int | None:
        """Return sensor value."""
        return self.entity_description.value_fn(self.coordinator.data)