"""Sensor platform for Sub-Zero BLE."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

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
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SubZeroConfigEntry
from .const import CONNECTION_STATUSES
from .coordinator import (
    SubZeroData,
    SubZeroDataUpdateCoordinator,
    build_info_attributes,
    build_info_desc,
    field_number,
    field_text,
    format_version,
    notif_attributes,
    notif_count,
    parse_uptime_seconds,
)
from .entity import SubZeroEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class SubZeroSensorEntityDescription(SensorEntityDescription):
    """Describes Sub-Zero sensor entity."""

    value_fn: Callable[[SubZeroData], float | int | str | None]
    attrs_fn: Callable[[SubZeroData], Mapping[str, Any]] | None = None


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
        value_fn=build_info_desc,
        attrs_fn=build_info_attributes,
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
        entity_registry_enabled_default=False,
        value_fn=lambda data: field_text(data, "max_ice_start_time"),
    ),
    SubZeroSensorEntityDescription(
        key="max_ice_end_time",
        name="Max Ice End Time",
        icon="mdi:clock-end",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
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
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=notif_count,
        attrs_fn=notif_attributes,
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


class SubZeroSensorEntity(SubZeroEntity, SensorEntity):
    """Representation of a Sub-Zero sensor."""

    entity_description: SubZeroSensorEntityDescription

    def __init__(
        self,
        coordinator: SubZeroDataUpdateCoordinator,
        description: SubZeroSensorEntityDescription,
        address: str,
    ) -> None:
        super().__init__(coordinator, address, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | int | str | None:
        """Return sensor value."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return per-notification attributes when the description provides them."""
        if self.entity_description.attrs_fn is None or self.coordinator.data is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)


class SubZeroConnectionSensor(SubZeroEntity, SensorEntity):
    """BLE connection and pairing status; stays available when a poll fails."""

    _attr_name = "Connection Status"
    _attr_icon = "mdi:bluetooth-connect"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(CONNECTION_STATUSES)
    _attr_entity_registry_enabled_default = False

    def __init__(
        self, coordinator: SubZeroDataUpdateCoordinator, address: str
    ) -> None:
        super().__init__(coordinator, address, "connection_status")

    @property
    def available(self) -> bool:
        """Keep the status readable while the rest of the device is unavailable."""
        return True

    @property
    def native_value(self) -> str:
        """Return the latest connection status."""
        return self.coordinator.connection_status
