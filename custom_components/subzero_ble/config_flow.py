"""Config flow for the Sub-Zero BLE integration."""

from typing import Any

import voluptuous as vol

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS

from .const import DOMAIN, LOCAL_NAME_PREFIX


def _is_subzero_device(discovery_info: BluetoothServiceInfoBleak) -> bool:
    """Return True if the advertisement belongs to a Sub-Zero Group appliance."""
    return discovery_info.name.startswith(LOCAL_NAME_PREFIX)


def _title(discovery_info: BluetoothServiceInfoBleak) -> str:
    """Return a human-readable title for the discovered appliance."""
    name = discovery_info.name
    if not name or name == discovery_info.address:
        return f"Sub-Zero ({discovery_info.address})"
    return name


class SubZeroBLEConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Sub-Zero BLE."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered_devices: dict[str, BluetoothServiceInfoBleak] = {}

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
            return self.async_create_entry(
                title=title,
                data={CONF_ADDRESS: discovery_info.address},
            )

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
            return self.async_create_entry(
                title=_title(discovery_info),
                data={CONF_ADDRESS: address},
            )

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
