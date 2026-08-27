"""Binary sensor platform for Sub-Zero BLE."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SubZeroConfigEntry
from .const import DOMAIN
from .coordinator import SubZeroData, SubZeroDataUpdateCoordinator


@dataclass(frozen=True, kw_only=True)
class SubZeroBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes Sub-Zero binary sensor entity."""
    is_on_fn: Callable[[SubZeroData], bool]


BINARY_SENSOR_DESCRIPTIONS: tuple[SubZeroBinarySensorEntityDescription, ...] = (
    SubZeroBinarySensorEntityDescription(
        key="fridge_door",
        name="Refrigerator Door",
        device_class=BinarySensorDeviceClass.DOOR,
        is_on_fn=lambda data: data.fridge_door_open,
    ),
    SubZeroBinarySensorEntityDescription(
        key="freezer_door",
        name="Freezer Door",
        device_class=BinarySensorDeviceClass.DOOR,
        is_on_fn=lambda data: data.freezer_door_open,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SubZeroConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Sub-Zero binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        SubZeroBinarySensorEntity(coordinator, desc, entry.data["address"])
        for desc in BINARY_SENSOR_DESCRIPTIONS
    )


class SubZeroBinarySensorEntity(
    CoordinatorEntity[SubZeroDataUpdateCoordinator], BinarySensorEntity
):
    """Representation of a Sub-Zero binary sensor."""

    entity_description: SubZeroBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: SubZeroDataUpdateCoordinator,
        description: SubZeroBinarySensorEntityDescription,
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
    def is_on(self) -> bool:
        """Return True if door is open."""
        return self.entity_description.is_on_fn(self.coordinator.data)