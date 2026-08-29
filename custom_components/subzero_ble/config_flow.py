"""Config flow for the Sub-Zero BLE integration."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any, Protocol

import voluptuous as vol
from bleak.exc import BleakError

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import async_discovered_service_info
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector

from .client import SubZeroBleClient, SubZeroInvalidPin
from .const import CONF_PIN, DOMAIN, LOCAL_NAME_PREFIX, normalize_pin
from .pairing import SubZeroPairingError

_LOGGER = logging.getLogger(__name__)


class BluetoothServiceInfoBleak(Protocol):
    """Bluetooth advertisement passed into discovery config-flow steps."""

    name: str
    address: str


def _is_subzero_device(discovery_info: BluetoothServiceInfoBleak) -> bool:
    """Return True if the advertisement belongs to a Sub-Zero Group appliance."""
    return discovery_info.name.startswith(LOCAL_NAME_PREFIX)


def _title(discovery_info: BluetoothServiceInfoBleak) -> str:
    """Return a human-readable title for the discovered appliance."""
    name = discovery_info.name
    if not name or name == discovery_info.address:
        return f"Sub-Zero ({discovery_info.address})"
    return name


def _pin_schema(default: str | None = None, *, required: bool = False) -> vol.Schema:
    """Return the PIN form schema."""
    pin_selector = selector.TextSelector(
        selector.TextSelectorConfig(
            type=selector.TextSelectorType.PASSWORD,
        )
    )
    if required:
        return vol.Schema({vol.Required(CONF_PIN): pin_selector})
    pin_default: Any = default or ""
    return vol.Schema({vol.Optional(CONF_PIN, default=pin_default): pin_selector})


async def _async_start_standalone_display_pin(
    hass: HomeAssistant, address: str
) -> tuple[asyncio.Task[None], SubZeroBleClient] | tuple[None, None]:
    """Start a background display_pin loop with a temporary BLE client."""
    ble_device = bluetooth.async_ble_device_from_address(
        hass, address, connectable=True
    )
    if ble_device is None:
        _LOGGER.warning(
            "Cannot start Show PIN during setup; %s is not currently in range",
            address,
        )
        return None, None

    client = SubZeroBleClient(ble_device)

    async def _run() -> None:
        try:
            await client.async_display_pin()
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.warning("Show PIN during setup stopped: %s", err)
        finally:
            await client.async_disconnect()

    task = hass.async_create_task(
        _run(), name=f"{DOMAIN} display_pin {address}"
    )
    return task, client


async def _async_verify_pin(hass: HomeAssistant, address: str, pin: str) -> None:
    """Connect, pair, and unlock. Raise if the appliance rejects the PIN."""
    ble_device = bluetooth.async_ble_device_from_address(
        hass, address, connectable=True
    )
    if ble_device is None:
        raise BleakError(f"Appliance {address} is not currently in range")
    client = SubZeroBleClient(ble_device, pin=pin)
    try:
        await client.async_verify_pin()
    finally:
        await client.async_disconnect()


def _pin_verify_error(err: BaseException) -> str:
    """Map a verify failure to a config-flow error key. Do not log the PIN."""
    if isinstance(err, (SubZeroInvalidPin, SubZeroPairingError)):
        _LOGGER.warning("Appliance rejected PIN: %s", err)
        return "invalid_auth"
    _LOGGER.warning("Could not verify PIN: %s", err)
    return "cannot_connect"


async def _async_cancel_task(task: asyncio.Task[None] | None) -> None:
    """Cancel a background task and wait for it to finish."""
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def _async_pause_runtime(entry: ConfigEntry) -> tuple[Any, Any]:
    """Stop polling and drop BLE so PIN verify can use the only connection."""
    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is None:
        return None, None
    interval = coordinator.update_interval
    coordinator.update_interval = None
    await coordinator.async_disconnect()
    return coordinator, interval


class SubZeroBLEConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Sub-Zero BLE."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered_devices: dict[str, BluetoothServiceInfoBleak] = {}
        self._address: str | None = None
        self._name: str | None = None
        self._display_pin_task: asyncio.Task[None] | None = None
        self._display_pin_client: SubZeroBleClient | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow for this config entry."""
        return SubZeroOptionsFlow(config_entry)

    async def _async_ensure_display_pin(self) -> None:
        """Start asking the appliance to show its PIN if not already running."""
        if self._address is None:
            return
        if self._display_pin_task is not None and not self._display_pin_task.done():
            return
        task, client = await _async_start_standalone_display_pin(
            self.hass, self._address
        )
        self._display_pin_task = task
        self._display_pin_client = client

    async def _async_stop_display_pin(self) -> None:
        """Stop a setup-time Show PIN loop and free the BLE connection."""
        task = self._display_pin_task
        self._display_pin_task = None
        client = self._display_pin_client
        self._display_pin_client = None
        await _async_cancel_task(task)
        if client is not None:
            await client.async_disconnect()

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle Bluetooth discovery of a Sub-Zero Group appliance."""
        if not _is_subzero_device(discovery_info):
            return self.async_abort(reason="not_supported")

        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        self._discovery_info = discovery_info
        self.context["title_placeholders"] = {"name": _title(discovery_info)}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a discovered appliance."""
        assert self._discovery_info is not None
        discovery_info = self._discovery_info
        title = _title(discovery_info)

        if user_input is not None:
            self._address = discovery_info.address
            self._name = title
            return await self.async_step_pin()

        self._set_confirm_only()
        placeholders = {"name": title}
        self.context["title_placeholders"] = placeholders
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders=placeholders,
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the user step to pick a discovered appliance."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            discovery_info = self._discovered_devices[address]
            self._address = address
            self._name = _title(discovery_info)
            return await self.async_step_pin()

        if request_active_scan := getattr(
            bluetooth, "async_request_active_scan", None
        ):
            await request_active_scan(self.hass)

        current_addresses = self._async_current_ids(include_ignore=False)
        if discovery := self._discovery_info:
            self._discovered_devices[discovery.address] = discovery

        for discovery_info in async_discovered_service_info(self.hass):
            address = discovery_info.address
            if (
                address in current_addresses
                or address in self._discovered_devices
                or not _is_subzero_device(discovery_info)
            ):
                continue
            self._discovered_devices[address] = discovery_info

        if not self._discovered_devices:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {
                            service_info.address: (
                                f"{_title(service_info)} ({service_info.address})"
                            )
                            for service_info in self._discovered_devices.values()
                        }
                    )
                }
            ),
        )

    async def async_step_pin(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the 6-digit appliance PIN used for BLE pairing and unlock."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                pin = normalize_pin(user_input.get(CONF_PIN))
            except ValueError:
                errors[CONF_PIN] = "invalid_pin"
            else:
                assert self._address is not None
                await self._async_stop_display_pin()
                if pin:
                    try:
                        await _async_verify_pin(self.hass, self._address, pin)
                    except (
                        SubZeroInvalidPin,
                        SubZeroPairingError,
                        BleakError,
                        TimeoutError,
                        OSError,
                    ) as err:
                        errors["base"] = _pin_verify_error(err)
                    else:
                        return self.async_create_entry(
                            title=self._name or self._address,
                            data={CONF_ADDRESS: self._address, CONF_PIN: pin},
                        )
                else:
                    return self.async_create_entry(
                        title=self._name or self._address,
                        data={CONF_ADDRESS: self._address},
                    )

        await self._async_ensure_display_pin()
        return self.async_show_form(
            step_id="pin",
            data_schema=_pin_schema(),
            errors=errors,
            description_placeholders={"name": self._name or "the appliance"},
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Perform reauthentication when the appliance rejects the PIN."""
        self._address = entry_data[CONF_ADDRESS]
        self._name = self._get_reauth_entry().title
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Prompt for a new PIN after the stored code was rejected."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()
        if user_input is not None:
            try:
                pin = normalize_pin(user_input.get(CONF_PIN))
            except ValueError:
                errors[CONF_PIN] = "invalid_pin"
            else:
                if not pin:
                    errors[CONF_PIN] = "invalid_pin"
                else:
                    await self._async_stop_display_pin()
                    coordinator, saved_interval = await _async_pause_runtime(
                        reauth_entry
                    )
                    try:
                        await _async_verify_pin(
                            self.hass,
                            self._address or reauth_entry.data[CONF_ADDRESS],
                            pin,
                        )
                    except (
                        SubZeroInvalidPin,
                        SubZeroPairingError,
                        BleakError,
                        TimeoutError,
                        OSError,
                    ) as err:
                        errors["base"] = _pin_verify_error(err)
                        if coordinator is not None:
                            coordinator.update_interval = saved_interval
                    else:
                        return self.async_update_reload_and_abort(
                            reauth_entry,
                            data_updates={CONF_PIN: pin},
                        )

        await self._async_ensure_display_pin()
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_pin_schema(required=True),
            errors=errors,
            description_placeholders={"name": self._name or "the appliance"},
        )


class SubZeroOptionsFlow(OptionsFlow):
    """Handle PIN changes for an existing Sub-Zero appliance."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry
        self._display_pin_task: asyncio.Task[None] | None = None
        self._display_pin_client: SubZeroBleClient | None = None

    async def _async_ensure_display_pin(self) -> None:
        """Start asking the appliance to show its PIN if not already running."""
        coordinator = getattr(self._config_entry, "runtime_data", None)
        if coordinator is not None and hasattr(
            coordinator, "async_ensure_display_pin"
        ):
            try:
                await coordinator.async_ensure_display_pin()
            except Exception as err:
                _LOGGER.warning("Show PIN from Configure stopped: %s", err)
            return

        if self._display_pin_task is not None and not self._display_pin_task.done():
            return
        address = self._config_entry.data.get(CONF_ADDRESS)
        if not address:
            return
        task, client = await _async_start_standalone_display_pin(self.hass, address)
        self._display_pin_task = task
        self._display_pin_client = client

    async def _async_stop_display_pin(self) -> None:
        """Stop a standalone Show PIN loop started by this options flow."""
        task = self._display_pin_task
        self._display_pin_task = None
        client = self._display_pin_client
        self._display_pin_client = None
        await _async_cancel_task(task)
        if client is not None:
            await client.async_disconnect()

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the pairing PIN."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                pin = normalize_pin(user_input.get(CONF_PIN))
            except ValueError:
                errors[CONF_PIN] = "invalid_pin"
            else:
                await self._async_stop_display_pin()
                coordinator, saved_interval = await _async_pause_runtime(
                    self._config_entry
                )
                try:
                    if pin:
                        await _async_verify_pin(
                            self.hass,
                            self._config_entry.data[CONF_ADDRESS],
                            pin,
                        )
                except (
                    SubZeroInvalidPin,
                    SubZeroPairingError,
                    BleakError,
                    TimeoutError,
                    OSError,
                ) as err:
                    errors["base"] = _pin_verify_error(err)
                    if coordinator is not None:
                        coordinator.update_interval = saved_interval
                else:
                    data = {**self._config_entry.data}
                    if pin:
                        data[CONF_PIN] = pin
                    else:
                        data.pop(CONF_PIN, None)
                    self.hass.config_entries.async_update_entry(
                        self._config_entry, data=data
                    )
                    return self.async_create_entry(title="", data={})

        await self._async_ensure_display_pin()
        return self.async_show_form(
            step_id="init",
            data_schema=_pin_schema(self._config_entry.data.get(CONF_PIN)),
            errors=errors,
        )
