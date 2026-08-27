"""Coordinator for Sub-Zero BLE."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
import json
import logging
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import SubZeroBleClient, SubZeroInvalidPin
from .const import CONF_PIN, DOMAIN, UPDATE_INTERVAL_SECONDS

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
    fields: dict[str, Any] = field(default_factory=dict)

    def has_values(self) -> bool:
        """Return True if any field was present in the payload."""
        if self.fields:
            return True
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
        fields = dict(self.fields)
        fields.update(update.fields)
        return SubZeroData(
            fridge_temp=_keep(update.fridge_temp, self.fridge_temp),
            freezer_temp=_keep(update.freezer_temp, self.freezer_temp),
            fridge_door_open=_keep(update.fridge_door_open, self.fridge_door_open),
            freezer_door_open=_keep(update.freezer_door_open, self.freezer_door_open),
            ice_maker_on=_keep(update.ice_maker_on, self.ice_maker_on),
            water_filter_life=_keep(update.water_filter_life, self.water_filter_life),
            air_filter_life=_keep(update.air_filter_life, self.air_filter_life),
            fields=fields,
        )


def _keep(new: object, old: object) -> object:
    return new if new is not None else old


def field_bool(data: SubZeroData, key: str) -> bool | None:
    """Return a boolean appliance field, or None if it was not in the payload."""
    if key not in data.fields:
        return None
    return bool(data.fields[key])


def field_text(data: SubZeroData, key: str) -> str | None:
    """Return an appliance field as a diagnostic string."""
    if key not in data.fields:
        return None
    value = data.fields[key]
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def field_number(data: SubZeroData, key: str) -> float | int | None:
    """Return a numeric appliance field."""
    if key not in data.fields:
        return None
    value = data.fields[key]
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_version(data: SubZeroData) -> str | None:
    """Return firmware/API version as a short diagnostic string."""
    if "version" not in data.fields:
        return None
    value = data.fields["version"]
    if isinstance(value, dict):
        parts: list[str] = []
        if fw := value.get("fw"):
            parts.append(f"fw {fw}")
        if api := value.get("api"):
            parts.append(f"api {api}")
        if appliance := value.get("appliance"):
            parts.append(str(appliance))
        if parts:
            return " / ".join(parts)
        return json.dumps(value, separators=(",", ":"))
    if value is None:
        return None
    return str(value)


def parse_uptime_seconds(data: SubZeroData) -> int | None:
    """Parse appliance uptime (`HH:MM:SS` or seconds) into seconds."""
    if "uptime" not in data.fields:
        return None
    value = data.fields["uptime"]
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        parts = value.split(":")
        if len(parts) == 3:
            try:
                hours, minutes, seconds = (int(part) for part in parts)
            except ValueError:
                return None
            return hours * 3600 + minutes * 60 + seconds
    return None


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

    async def _async_ready_client(self, *, require_pin: bool = False) -> SubZeroBleClient:
        """Return a BLE client, creating it if needed."""
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            raise HomeAssistantError(
                f"Sub-Zero appliance {self.address} is not currently in range"
            )
        pin = self._pin()
        if require_pin and not pin:
            raise HomeAssistantError(
                "Enter the 6-digit PIN under Configure first. "
                "Writes use encrypted channel D5."
            )
        if self.client is None:
            self.client = SubZeroBleClient(
                ble_device, on_push=self._handle_push, pin=pin
            )
        else:
            self.client.update_ble_device(ble_device)
            if self.client.update_pin(pin):
                await self.client.async_disconnect()
        return self.client

    async def async_display_pin(self) -> None:
        """Pair if needed, then ask the appliance to show its PIN on the display."""
        client = await self._async_ready_client(require_pin=True)
        try:
            await client.async_display_pin()
        except Exception as err:
            raise HomeAssistantError(str(err)) from err

    async def async_set_temperature(self, property_key: str, value: float) -> None:
        """Write a fridge or freezer setpoint on D5."""
        wire = int(round(value))
        client = await self._async_ready_client(require_pin=True)
        try:
            await client.async_set_property(property_key, wire)
        except Exception as err:
            raise HomeAssistantError(str(err)) from err
        if property_key == "ref_set_temp":
            update = SubZeroData(fridge_temp=float(wire), fields={property_key: wire})
        elif property_key == "frz_set_temp":
            update = SubZeroData(freezer_temp=float(wire), fields={property_key: wire})
        else:
            update = SubZeroData(fields={property_key: wire})
        self._apply_push(update)

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
        self._sync_device_info(merged)
        self.async_set_updated_data(merged)

    def _sync_device_info(self, data: SubZeroData) -> None:
        """Copy model/serial/firmware from the appliance into the device registry."""
        registry = dr.async_get(self.hass)
        device = registry.async_get_device(identifiers={(DOMAIN, self.address)})
        if device is None:
            return
        updates: dict[str, str] = {}
        if model := data.fields.get("appliance_model"):
            updates["model"] = str(model)
        if serial := data.fields.get("appliance_serial"):
            updates["serial_number"] = str(serial)
        version = format_version(data)
        if version:
            updates["sw_version"] = version
        if updates:
            registry.async_update_device(device.id, **updates)

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
        self._sync_device_info(merged)
        _LOGGER.info(
            "Sub-Zero poll complete for %s: fridge_door=%s freezer_door=%s",
            self.address,
            merged.fridge_door_open,
            merged.freezer_door_open,
        )
        return merged
