"""Coordinator for Sub-Zero BLE."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import SubZeroBleClient, SubZeroInvalidPin
from .const import CONF_PIN, UPDATE_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)


@dataclass
class SubZeroData:
    """Parsed state data from appliance."""

    fridge_temp: float | None = None
    freezer_temp: float | None = None
    fridge_door_open: bool | None = None
    freezer_door_open: bool | None = None
    ice_maker_on: bool | None = None
    water_filter_life: int | None = None
    air_filter_life: int | None = None

    def has_values(self) -> bool:
        """Return True if any field was present in the payload."""
        return any(
            value is not None
            for value in (
                self.fridge_temp,
                self.freezer_temp,
                self.fridge_door_open,
                self.freezer_door_open,
                self.ice_maker_on,
                self.water_filter_life,
                self.air_filter_life,
            )
        )

    def merge(self, update: SubZeroData) -> SubZeroData:
        """Overlay non-None fields from a poll or push onto current state."""
        return SubZeroData(
            fridge_temp=_keep(update.fridge_temp, self.fridge_temp),
            freezer_temp=_keep(update.freezer_temp, self.freezer_temp),
            fridge_door_open=_keep(update.fridge_door_open, self.fridge_door_open),
            freezer_door_open=_keep(update.freezer_door_open, self.freezer_door_open),
            ice_maker_on=_keep(update.ice_maker_on, self.ice_maker_on),
            water_filter_life=_keep(update.water_filter_life, self.water_filter_life),
            air_filter_life=_keep(update.air_filter_life, self.air_filter_life),
        )


def _keep(new: object, old: object) -> object:
    return new if new is not None else old


class SubZeroDataUpdateCoordinator(DataUpdateCoordinator[SubZeroData]):
    """Manage fetching Sub-Zero appliance data via BLE."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=entry.title,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        self.entry = entry
        self.address = entry.data[CONF_ADDRESS]
        self.client: SubZeroBleClient | None = None

    def _pin(self) -> str | None:
        """Return the configured pairing PIN, if any."""
        return self.entry.data.get(CONF_PIN) or self.entry.options.get(CONF_PIN)

    async def async_display_pin(self) -> None:
        """Ask the appliance to show its PIN on the display."""
        if self.client is None:
            raise HomeAssistantError("Sub-Zero client is not connected")
        try:
            await self.client.async_display_pin()
        except Exception as err:
            raise HomeAssistantError(str(err)) from err

    def _apply_push(self, data: SubZeroData) -> None:
        """Merge an unsolicited notification into coordinator state."""
        current = self.data or SubZeroData()
        merged = current.merge(data)
        _LOGGER.info(
            "Applying Sub-Zero push for %s: fridge_door=%s freezer_door=%s",
            self.address,
            merged.fridge_door_open,
            merged.freezer_door_open,
        )
        self.async_set_updated_data(merged)

    def _handle_push(self, data: SubZeroData) -> None:
        """Receive a push from the BLE thread/loop and apply it on the HA loop."""
        self.hass.loop.call_soon_threadsafe(self._apply_push, data)

    async def async_disconnect(self) -> None:
        """Disconnect the BLE client."""
        if self.client is not None:
            await self.client.async_disconnect()

    async def _async_update_data(self) -> SubZeroData:
        _LOGGER.info("Starting Sub-Zero update for %s", self.address)
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            _LOGGER.warning(
                "Sub-Zero appliance %s is not currently visible over Bluetooth",
                self.address,
            )
            raise UpdateFailed(f"Sub-Zero appliance {self.address} not in range")

        pin = self._pin()
        if self.client is None:
            self.client = SubZeroBleClient(
                ble_device, on_push=self._handle_push, pin=pin
            )
        else:
            self.client.update_ble_device(ble_device)
            if self.client.update_pin(pin):
                await self.client.async_disconnect()

        try:
            parsed = await self.client.poll_state()
        except SubZeroInvalidPin as err:
            if self.client is not None:
                await self.client.async_disconnect()
            raise UpdateFailed(str(err)) from err
        except Exception as err:
            if self.client is not None:
                await self.client.async_disconnect()
            raise UpdateFailed(f"BLE communication error: {err}") from err

        current = self.data or SubZeroData()
        merged = current.merge(parsed)
        _LOGGER.info(
            "Sub-Zero poll complete for %s: fridge_door=%s freezer_door=%s",
            self.address,
            merged.fridge_door_open,
            merged.freezer_door_open,
        )
        return merged
