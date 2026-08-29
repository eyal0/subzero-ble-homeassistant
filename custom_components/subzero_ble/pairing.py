"""BlueZ passkey agent for Sub-Zero Legacy Pairing with MITM."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError

from .const import PAIR_TIMEOUT

_LOGGER = logging.getLogger(__name__)

AGENT_PATH = "/com/subzero_ble/agent"


class SubZeroPairingError(BleakError):
    """Raised when BLE pairing fails."""


def bleak_message_bus(client: object | None) -> Any | None:
    """Return the dbus-fast MessageBus used by a connected Bleak client."""
    current: object | None = client
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        bus = getattr(current, "_bus", None)
        if bus is not None and callable(getattr(bus, "export", None)):
            return bus
        current = getattr(current, "_backend", None) or getattr(
            current, "_client", None
        )
    return None


def _device_path(ble_device: BLEDevice) -> str | None:
    """Return the BlueZ object path for a Bleak device, if available."""
    details = ble_device.details
    if isinstance(details, dict):
        path = details.get("path")
        if isinstance(path, str):
            return path
    return None


class PairingAgentSession:
    """KeyboardOnly org.bluez.Agent1 registered on a D-Bus connection.

    The agent must live on the same MessageBus that performs GATT and Pair()
    so BlueZ routes RequestPasskey to us instead of the desktop default agent.
    """

    def __init__(self, pin: str) -> None:
        self._pin = pin
        self._bus: Any | None = None
        self._owns_bus = False
        self._manager: Any | None = None
        self._exported = False

    async def start(self, bus: Any | None = None) -> None:
        """Export the agent and register it with BlueZ."""
        from dbus_fast import BusType
        from dbus_fast.aio.message_bus import MessageBus
        from dbus_fast.errors import DBusError
        from dbus_fast.service import ServiceInterface, method

        class PasskeyAgent(ServiceInterface):
            """org.bluez.Agent1 that types the 6-digit appliance PIN."""

            def __init__(self, pin: str) -> None:
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
            def DisplayPasskey(
                self, device: "o", passkey: "u", entered: "q"
            ) -> "":  # noqa: F821
                _LOGGER.info(
                    "BlueZ DisplayPasskey %06d entered=%s", int(passkey), entered
                )

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

        if bus is None:
            self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            self._owns_bus = True
            _LOGGER.warning(
                "Pairing agent is on a dedicated D-Bus connection; "
                "GATT pairing may not encrypt this link"
            )
        else:
            self._bus = bus
            self._owns_bus = False
            _LOGGER.info("Pairing agent attached to the GATT D-Bus connection")

        try:
            self._bus.unexport(AGENT_PATH)
        except Exception:
            pass
        self._bus.export(AGENT_PATH, PasskeyAgent(self._pin))
        self._exported = True

        introspection = await self._bus.introspect("org.bluez", "/org/bluez")
        bluez = self._bus.get_proxy_object("org.bluez", "/org/bluez", introspection)
        self._manager = bluez.get_interface("org.bluez.AgentManager1")
        try:
            await self._manager.call_register_agent(AGENT_PATH, "KeyboardOnly")
        except Exception as err:
            _LOGGER.debug("RegisterAgent: %s", err)
            try:
                await self._manager.call_unregister_agent(AGENT_PATH)
            except Exception:
                pass
            await self._manager.call_register_agent(AGENT_PATH, "KeyboardOnly")
        try:
            await self._manager.call_request_default_agent(AGENT_PATH)
        except Exception as err:
            _LOGGER.debug("RequestDefaultAgent not granted (%s); continuing", err)
        _LOGGER.info("Registered BlueZ KeyboardOnly pairing agent")

    async def stop(self) -> None:
        """Unregister the agent. Never disconnect a borrowed Bleak bus."""
        manager = self._manager
        bus = self._bus
        self._manager = None
        if manager is not None:
            try:
                await manager.call_unregister_agent(AGENT_PATH)
            except Exception:
                _LOGGER.debug("UnregisterAgent failed", exc_info=True)
        if self._exported and bus is not None:
            try:
                bus.unexport(AGENT_PATH)
            except Exception:
                pass
        self._exported = False
        if self._owns_bus and bus is not None:
            try:
                bus.disconnect()
            except Exception:
                pass
        self._bus = None


async def async_device_is_paired(
    ble_device: BLEDevice, client: Any | None = None
) -> bool:
    """Return True if BlueZ already has a bond for this appliance."""
    device_path = _device_path(ble_device)
    if device_path is None:
        return False
    bus = bleak_message_bus(client)
    owns_bus = False
    if bus is None:
        from dbus_fast import BusType
        from dbus_fast.aio.message_bus import MessageBus

        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        owns_bus = True
    try:
        introspection = await bus.introspect("org.bluez", device_path)
        obj = bus.get_proxy_object("org.bluez", device_path, introspection)
        device = obj.get_interface("org.bluez.Device1")
        return bool(await device.get_paired())
    except Exception:
        _LOGGER.debug("Could not read BlueZ Paired for %s", device_path, exc_info=True)
        return False
    finally:
        if owns_bus:
            try:
                bus.disconnect()
            except Exception:
                pass


async def async_pair_with_passkey(
    ble_device: BLEDevice,
    pin: str,
    client: Any | None = None,
    *,
    force: bool = False,
    session: PairingAgentSession | None = None,
) -> bool:
    """Bond using Legacy Pairing and the 6-digit PIN.

    Prefer Bleak's pair() so Pair() is issued on the same D-Bus connection as
    GATT. A separate bus is used only when the Bleak bus cannot be found.

    Return True if a new SMP pairing completed (the ATT link should reconnect).
    Return False if a bond already existed.
    """
    device_path = _device_path(ble_device)
    if device_path is None:
        raise SubZeroPairingError(
            "Cannot pair: BlueZ device path is unavailable on this adapter"
        )

    if not force and await async_device_is_paired(ble_device, client):
        _LOGGER.info("Bond already exists for %s", ble_device.address)
        return False

    created_session = session is None
    if session is None:
        session = PairingAgentSession(pin)
        await session.start(bleak_message_bus(client))
    try:
        if force and client is not None and hasattr(client, "unpair"):
            _LOGGER.info("Removing existing BlueZ bond for %s", ble_device.address)
            try:
                await client.unpair()
            except Exception as err:
                _LOGGER.debug("unpair before force-pair: %s", err)

        _LOGGER.info("Starting BLE passkey pairing with %s", ble_device.address)
        if client is not None and hasattr(client, "pair"):
            await asyncio.wait_for(client.pair(), timeout=PAIR_TIMEOUT)
        else:
            await _pair_via_device_interface(session._bus, device_path)
    except TimeoutError as err:
        raise SubZeroPairingError(
            "Timed out waiting for BLE pairing. "
            "Watch the appliance display and confirm the 6-digit PIN in options."
        ) from err
    except SubZeroPairingError:
        raise
    except Exception as err:
        message = str(err)
        if "AlreadyExists" in message or "already" in message.lower():
            _LOGGER.info("Bond already exists for %s", ble_device.address)
            return False
        raise SubZeroPairingError(f"BLE pairing failed: {err}") from err
    finally:
        # A caller-owned session stays registered on the GATT bus. A dedicated
        # bus created here is closed so it does not leak.
        if created_session and session._owns_bus:
            await session.stop()

    _LOGGER.info("BLE pairing completed for %s", ble_device.address)
    return True


async def _pair_via_device_interface(bus: Any, device_path: str) -> None:
    """Call org.bluez.Device1.Pair on an already-open MessageBus."""
    introspection = await bus.introspect("org.bluez", device_path)
    obj = bus.get_proxy_object("org.bluez", device_path, introspection)
    device = obj.get_interface("org.bluez.Device1")
    try:
        if await device.get_paired():
            _LOGGER.info("Already bonded at %s", device_path)
            return
    except Exception:
        _LOGGER.debug("Could not read Paired property", exc_info=True)
    await asyncio.wait_for(device.call_pair(), timeout=PAIR_TIMEOUT)
