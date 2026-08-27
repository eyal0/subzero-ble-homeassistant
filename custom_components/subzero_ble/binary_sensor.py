"""Binary sensor platform for Sub-Zero BLE."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SubZeroConfigEntry
from .const import DOMAIN
from .coordinator import (
    SubZeroData,
    SubZeroDataUpdateCoordinator,
    field_bool,
    field_nonempty,
)


@dataclass(frozen=True, kw_only=True)
class SubZeroBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes Sub-Zero binary sensor entity."""

    is_on_fn: Callable[[SubZeroData], bool | None]


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
    SubZeroBinarySensorEntityDescription(
        key="ice_maker_on",
        name="Ice Maker",
        icon="mdi:snowflake",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        is_on_fn=lambda data: field_bool(data, "ice_maker_on"),
    ),
    SubZeroBinarySensorEntityDescription(
        key="max_ice_on",
        name="Max Ice Mode",
        icon="mdi:snowflake-variant",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        is_on_fn=lambda data: field_bool(data, "max_ice_on"),
    ),
    SubZeroBinarySensorEntityDescription(
        key="night_ice_on",
        name="Night Ice Mode",
        icon="mdi:weather-night",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        is_on_fn=lambda data: field_bool(data, "night_ice_on"),
    ),
    SubZeroBinarySensorEntityDescription(
        key="sabbath_on",
        name="Sabbath Mode",
        icon="mdi:star-david",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        is_on_fn=lambda data: field_bool(data, "sabbath_on"),
    ),
    SubZeroBinarySensorEntityDescription(
        key="service_mode",
        name="Service Mode",
        icon="mdi:wrench-cog-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda data: field_bool(data, "service_mode"),
    ),
    SubZeroBinarySensorEntityDescription(
        key="service_required",
        name="Service Required",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda data: field_bool(data, "service_required"),
    ),
    SubZeroBinarySensorEntityDescription(
        key="has_active_faults",
        name="Active Faults",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda data: field_nonempty(data, "active_faults"),
    ),
    SubZeroBinarySensorEntityDescription(
        key="has_notifications",
        name="Notifications",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda data: field_nonempty(data, "notifs"),
    ),
    SubZeroBinarySensorEntityDescription(
        key="pin_window_open",
        name="Pairing Window",
        icon="mdi:key-plus",
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda data: field_bool(data, "pin_window_open"),
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
    def is_on(self) -> bool | None:
        """Return True if the binary sensor is on."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.is_on_fn(self.coordinator.data)
