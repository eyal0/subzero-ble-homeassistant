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
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SubZeroConfigEntry
from .const import CONNECTION_STATUSES, DOMAIN
from .coordinator import (
    SubZeroData,
    SubZeroDataUpdateCoordinator,
    field_number,
    field_text,
    format_version,
    parse_uptime_seconds,
)


@dataclass(frozen=True, kw_only=True)
class SubZeroSensorEntityDescription(SensorEntityDescription):
    """Describes Sub-Zero sensor entity."""

    value_fn: Callable[[SubZeroData], float | int | str | None]


# Writable fridge/freezer setpoints live on the number platform.
SENSOR_DESCRIPTIONS: tuple[SubZeroSensorEntityDescription, ...] = (
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
    SubZeroSensorEntityDescription(
        key="appliance_model",
        name="Appliance Model",
        icon="mdi:fridge-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: field_text(data, "appliance_model"),
    ),
    SubZeroSensorEntityDescription(
        key="appliance_name",
        name="Appliance Name",
        icon="mdi:tag-text-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: field_text(data, "appliance_name"),
    ),
    SubZeroSensorEntityDescription(
        key="appliance_serial",
        name="Appliance Serial",
        icon="mdi:barcode",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: field_text(data, "appliance_serial"),
    ),
    SubZeroSensorEntityDescription(
        key="appliance_type",
        name="Appliance Type",
        icon="mdi:information-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: field_text(data, "appliance_type"),
    ),
    SubZeroSensorEntityDescription(
        key="build_info",
        name="Build Info",
        icon="mdi:code-braces",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: field_text(data, "build_info"),
    ),
    SubZeroSensorEntityDescription(
        key="door_ajar_timeout",
        name="Door Ajar Alarm Timeout",
        icon="mdi:timer-alert-outline",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: field_number(data, "door_ajar_timeout"),
    ),
    SubZeroSensorEntityDescription(
        key="max_ice_start_time",
        name="Max Ice Start Time",
        icon="mdi:clock-start",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: field_text(data, "max_ice_start_time"),
    ),
    SubZeroSensorEntityDescription(
        key="max_ice_end_time",
        name="Max Ice End Time",
        icon="mdi:clock-end",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: field_text(data, "max_ice_end_time"),
    ),
    SubZeroSensorEntityDescription(
        key="service",
        name="Service",
        icon="mdi:account-wrench-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: field_text(data, "service"),
    ),
    SubZeroSensorEntityDescription(
        key="active_faults",
        name="Active Faults",
        icon="mdi:alert-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: field_text(data, "active_faults"),
    ),
    SubZeroSensorEntityDescription(
        key="notifs",
        name="Notifications",
        icon="mdi:bell-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: field_text(data, "notifs"),
    ),
    SubZeroSensorEntityDescription(
        key="uptime",
        name="Uptime",
        icon="mdi:timer-outline",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=parse_uptime_seconds,
    ),
    SubZeroSensorEntityDescription(
        key="version",
        name="Firmware Version",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=format_version,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SubZeroConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Sub-Zero sensors from config entry."""
    coordinator = entry.runtime_data
    address = entry.data["address"]
    async_add_entities(
        [
            *(
                SubZeroSensorEntity(coordinator, desc, address)
                for desc in SENSOR_DESCRIPTIONS
            ),
            SubZeroConnectionSensor(coordinator, address),
        ]
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
    def native_value(self) -> float | int | str | None:
        """Return sensor value."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)


class SubZeroConnectionSensor(
    CoordinatorEntity[SubZeroDataUpdateCoordinator], SensorEntity
):
    """BLE connection and pairing status; stays available when a poll fails."""

    _attr_name = "Connection Status"
    _attr_icon = "mdi:bluetooth-connect"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(CONNECTION_STATUSES)

    def __init__(
        self, coordinator: SubZeroDataUpdateCoordinator, address: str
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{address}_connection_status"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            name="Sub-Zero Refrigerator",
            manufacturer="Sub-Zero",
        )

    @property
    def available(self) -> bool:
        """Keep the status readable while the rest of the device is unavailable."""
        return True

    @property
    def native_value(self) -> str:
        """Return the latest connection status."""
        return self.coordinator.connection_status
