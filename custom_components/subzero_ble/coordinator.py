"""Coordinator for Sub-Zero BLE."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import SubZeroBleClient

_LOGGER = logging.getLogger(__name__)


@dataclass
class SubZeroData:
    """Parsed state data from appliance."""
    fridge_temp: float | None = None
    freezer_temp: float | None = None
    fridge_door_open: bool = False
    freezer_door_open: bool = False
    ice_maker_on: bool = False
    water_filter_life: int | None = None
    air_filter_life: int | None = None


class SubZeroDataUpdateCoordinator(DataUpdateCoordinator[SubZeroData]):
    """Manage fetching Sub-Zero appliance data via BLE."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=entry.title,
            update_interval=timedelta(seconds=60),
        )
        self.entry = entry
        self.address = entry.data[CONF_ADDRESS]
        self.client: SubZeroBleClient | None = None

    async def _async_update_data(self) -> SubZeroData:
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            raise UpdateFailed(f"Sub-Zero appliance {self.address} not in range")

        if self.client is None:
            self.client = SubZeroBleClient(ble_device)
        else:
            self.client.update_ble_device(ble_device)

        try:
            return await self.client.poll_state()
        except Exception as err:
            raise UpdateFailed(f"BLE communication error: {err}") from err