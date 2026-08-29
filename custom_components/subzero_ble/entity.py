"""Base entity for the Sub-Zero BLE integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SubZeroDataUpdateCoordinator


class SubZeroEntity(CoordinatorEntity[SubZeroDataUpdateCoordinator]):
    """Entity bound to a Sub-Zero appliance coordinator."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SubZeroDataUpdateCoordinator,
        address: str,
        unique_id_suffix: str,
    ) -> None:
        """Bind this entity to the appliance."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{address}_{unique_id_suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            name="Sub-Zero Refrigerator",
            manufacturer="Sub-Zero",
        )
