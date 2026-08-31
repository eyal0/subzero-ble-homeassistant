"""Tests for GATT connect error handling."""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

try:
    from bleak.exc import BleakError
except ModuleNotFoundError:  # pragma: no cover
    _exc = types.ModuleType("bleak.exc")

    class BleakError(Exception):
        """Stand-in when bleak is not installed."""

    _exc.BleakError = BleakError
    sys.modules.setdefault("bleak", types.ModuleType("bleak"))
    sys.modules["bleak.exc"] = _exc

if "bleak_retry_connector" not in sys.modules:
    try:
        import bleak_retry_connector  # noqa: F401
    except ModuleNotFoundError:
        _brc = types.ModuleType("bleak_retry_connector")

        class BleakClientWithServiceCache:
            """Stand-in when bleak-retry-connector is not installed."""

        async def establish_connection(*_args: object, **_kwargs: object):
            raise AssertionError("establish_connection should be patched")

        _brc.BleakClientWithServiceCache = BleakClientWithServiceCache
        _brc.establish_connection = establish_connection
        sys.modules["bleak_retry_connector"] = _brc

try:
    from custom_components.subzero_ble.client import SubZeroBleClient
except ModuleNotFoundError:
    # Allow `python3 -m unittest` without Home Assistant installed.
    _pkg_dir = Path(__file__).resolve().parents[1] / "custom_components" / "subzero_ble"
    _parent = types.ModuleType("custom_components")
    _parent.__path__ = [str(_pkg_dir.parent)]
    sys.modules.setdefault("custom_components", _parent)
    _pkg = types.ModuleType("custom_components.subzero_ble")
    _pkg.__path__ = [str(_pkg_dir)]
    sys.modules["custom_components.subzero_ble"] = _pkg
    from custom_components.subzero_ble.client import SubZeroBleClient

ADDRESS = "00:06:80:2D:15:F5"
PIN = "123456"


def _ble_device() -> SimpleNamespace:
    return SimpleNamespace(name="SZG DEU2450C", address=ADDRESS, rssi=None)


def _connected_gatt_client() -> MagicMock:
    client = MagicMock()
    client.is_connected = True
    client.services = []
    client.mtu_size = 23
    return client


class RequireConnectedTests(unittest.TestCase):
    def test_require_connected_raises_bleak_error_when_link_dropped(self) -> None:
        """A drop during settle used to surface as AssertionError with no message."""
        client = SubZeroBleClient(_ble_device(), pin=PIN)

        with self.assertRaisesRegex(BleakError, "Sub-Zero disconnected"):
            client._require_connected()

        gatt = _connected_gatt_client()
        gatt.is_connected = False
        client._client = gatt
        with self.assertRaisesRegex(BleakError, "Sub-Zero disconnected"):
            client._require_connected()


class ConnectSetupTests(unittest.IsolatedAsyncioTestCase):
    async def test_ensure_paired_after_disconnect_is_bleak_error(self) -> None:
        """Pairing after an unexpected drop must not raise a bare AssertionError."""
        client = SubZeroBleClient(_ble_device(), pin=PIN)

        with self.assertRaisesRegex(BleakError, "Sub-Zero disconnected"):
            await client._ensure_paired()

    async def test_connect_drop_during_settle_is_bleak_error(self) -> None:
        """If the fridge drops the link during LINK_SETTLE, report a disconnect."""
        client = SubZeroBleClient(_ble_device(), pin=PIN)
        gatt = _connected_gatt_client()

        async def drop_during_settle(_seconds: float) -> None:
            client._disconnected(gatt)

        with (
            patch(
                "custom_components.subzero_ble.client.establish_connection",
                new=AsyncMock(return_value=gatt),
            ),
            patch(
                "custom_components.subzero_ble.client.asyncio.sleep",
                new=drop_during_settle,
            ),
            self.assertLogs(
                "custom_components.subzero_ble.client", level="WARNING"
            ) as logs,
        ):
            with self.assertRaisesRegex(BleakError, "Sub-Zero disconnected"):
                await client._connect_and_setup()
        self.assertTrue(
            any("Disconnected from Sub-Zero" in line for line in logs.output)
        )
