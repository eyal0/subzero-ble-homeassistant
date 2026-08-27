"""BlueZ passkey agent for Sub-Zero Legacy Pairing with MITM."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError

from .const import PAIR_TIMEOUT

_LOGGER = logging.getLogger(__name__)

AGENT_PATH = "/com/subzero_ble/agent"


class SubZeroPairingError(BleakError):
    """Raised when BLE passkey pairing fails."""


def _device_path(ble_device: BLEDevice) -> str | None:
    """Return the BlueZ object path for a Bleak device, if available."""
    details = ble_device.details
    if isinstance(details, dict):
        path = details.get("path")
        if isinstance(path, str):
            return path
    return None


@asynccontextmanager
async def _passkey_agent(pin: str) -> AsyncIterator[Any]:
    """Register a KeyboardOnly BlueZ agent that returns the appliance PIN."""
    from dbus_fast import BusType
    from dbus_fast.aio import MessageBus
    from dbus_fast.errors import DBusError
    from dbus_fast.service import ServiceInterface, method

    class PasskeyAgent(ServiceInterface):
        """org.bluez.Agent1 that types the 6-digit appliance PIN."""

        def __init__(self) -> None:
            super().__init__("org.bluez.Agent1")
            self._pin = pin

        # dbus-fast requires D-Bus signature strings. Do not use `-> None`:
        # with PEP 563 that becomes the string "None", which dbus-fast evals
        # to None and Cython then rejects as a signature.
        @method()
        def Release(self) -> "":
            return None

        @method()
        def RequestPinCode(self, device: "o") -> "s":  # noqa: F821
            _LOGGER.info("BlueZ RequestPinCode for %s", device)
            return self._pin

        @method()
        def RequestPasskey(self, device: "o") -> "u":  # noqa: F821
            _LOGGER.info("BlueZ RequestPasskey for %s", device)
            return int(self._pin)

        @method()
        def DisplayPasskey(self, device: "o", passkey: "u", entered: "q") -> "":  # noqa: F821
            _LOGGER.info("BlueZ DisplayPasskey %06d entered=%s", int(passkey), entered)

        @method()
        def DisplayPinCode(self, device: "o", pincode: "s") -> "":  # noqa: F821
            _LOGGER.info("BlueZ DisplayPinCode")

        @method()
        def RequestConfirmation(self, device: "o", passkey: "u") -> "":  # noqa: F821
            shown = f"{int(passkey):06d}"
            _LOGGER.info("BlueZ RequestConfirmation passkey=%s", shown)
            if shown != self._pin:
                raise DBusError("org.bluez.Error.Rejected", "Passkey mismatch")

        @method()
        def RequestAuthorization(self, device: "o") -> "":  # noqa: F821
            return None

        @method()
        def AuthorizeService(self, device: "o", uuid: "s") -> "":  # noqa: F821
            return None

        @method()
        def Cancel(self) -> "":
            return None

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    agent = PasskeyAgent()
    bus.export(AGENT_PATH, agent)
    introspection = await bus.introspect("org.bluez", "/org/bluez")
    bluez = bus.get_proxy_object("org.bluez", "/org/bluez", introspection)
    manager = bluez.get_interface("org.bluez.AgentManager1")
    try:
        await manager.call_register_agent(AGENT_PATH, "KeyboardOnly")
    except Exception as err:
        _LOGGER.debug("RegisterAgent: %s", err)
        try:
            await manager.call_unregister_agent(AGENT_PATH)
        except Exception:
            pass
        await manager.call_register_agent(AGENT_PATH, "KeyboardOnly")
    try:
        await manager.call_request_default_agent(AGENT_PATH)
    except Exception as err:
        _LOGGER.debug("RequestDefaultAgent not granted (%s); continuing", err)
    try:
        yield bus
    finally:
        try:
            await manager.call_unregister_agent(AGENT_PATH)
        except Exception:
            _LOGGER.debug("UnregisterAgent failed", exc_info=True)
        try:
            bus.unexport(AGENT_PATH)
        except Exception:
            pass
        bus.disconnect()


async def async_pair_with_passkey(ble_device: BLEDevice, pin: str) -> None:
    """Bond to the appliance using Legacy Pairing and the 6-digit PIN.

    The appliance displays the passkey; this client types it (KeyboardOnly),
    matching ESPHome's io_capability: keyboard_only.
    """
    device_path = _device_path(ble_device)
    if device_path is None:
        raise SubZeroPairingError(
            "Cannot pair: BlueZ device path is unavailable on this adapter"
        )

    _LOGGER.info("Starting BLE passkey pairing with %s", ble_device.address)
    try:
        async with _passkey_agent(pin) as bus:
            introspection = await bus.introspect("org.bluez", device_path)
            obj = bus.get_proxy_object("org.bluez", device_path, introspection)
            device = obj.get_interface("org.bluez.Device1")
            try:
                if await device.get_paired():
                    _LOGGER.info("Already bonded to %s", ble_device.address)
                    return
            except Exception:
                _LOGGER.debug("Could not read Paired property", exc_info=True)
            try:
                await asyncio.wait_for(device.call_pair(), timeout=PAIR_TIMEOUT)
            except TimeoutError as err:
                raise SubZeroPairingError(
                    "Timed out waiting for BLE pairing. "
                    "Check the 6-digit PIN on the appliance display."
                ) from err
    except SubZeroPairingError:
        raise
    except Exception as err:
        message = str(err)
        if "AlreadyExists" in message or "already" in message.lower():
            _LOGGER.info("Bond already exists for %s", ble_device.address)
            return
        raise SubZeroPairingError(f"BLE pairing failed: {err}") from err

    _LOGGER.info("BLE pairing completed for %s", ble_device.address)
