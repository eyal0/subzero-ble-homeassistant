"""Tests for BlueZ path/bus discovery used when adding a PIN later."""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, patch

try:
    from custom_components.subzero_ble.pairing import (
        SubZeroPairingError,
        _device_path,
        async_pair_with_passkey,
        bleak_message_bus,
    )
except ModuleNotFoundError:
    # Allow `python3 -m unittest` without Home Assistant installed.
    _pkg_dir = Path(__file__).resolve().parents[1] / "custom_components" / "subzero_ble"
    _parent = types.ModuleType("custom_components")
    _parent.__path__ = [str(_pkg_dir.parent)]
    sys.modules.setdefault("custom_components", _parent)
    _pkg = types.ModuleType("custom_components.subzero_ble")
    _pkg.__path__ = [str(_pkg_dir)]
    sys.modules["custom_components.subzero_ble"] = _pkg
    from custom_components.subzero_ble.pairing import (
        SubZeroPairingError,
        _device_path,
        async_pair_with_passkey,
        bleak_message_bus,
    )

ADDRESS = "00:06:80:2D:15:F5"
BLUEZ_PATH = "/org/bluez/hci0/dev_00_06_80_2D_15_F5"
PIN = "123456"


def _device(details: object) -> SimpleNamespace:
    return SimpleNamespace(address=ADDRESS, name="SZG DEU2450C", details=details)


class DevicePathTests(unittest.TestCase):
    def test_device_path_from_mapping_proxy(self) -> None:
        """Home Assistant freezes scanner details as a mapping, not a plain dict."""
        device = _device(MappingProxyType({"path": BLUEZ_PATH, "source": "local"}))
        self.assertEqual(_device_path(device), BLUEZ_PATH)

    def test_device_path_from_connected_backend(self) -> None:
        """HaBleakClientWrapper keeps the path on the BlueZ backend."""
        device = _device(MappingProxyType({"source": "local"}))
        client = SimpleNamespace(
            _backend=SimpleNamespace(_device_path=BLUEZ_PATH, _bus=None)
        )
        self.assertEqual(_device_path(device, client), BLUEZ_PATH)

    def test_device_path_from_connected_device(self) -> None:
        """Prefer the BLEDevice HA actually connected with."""
        scanner = _device({"source": "00:00:00:00:00:01"})
        connected = _device({"path": BLUEZ_PATH})
        client = SimpleNamespace(_connected_device=connected, _backend=None)
        self.assertEqual(_device_path(scanner, client), BLUEZ_PATH)

    def test_bleak_message_bus_walks_ha_wrapper(self) -> None:
        """GATT pairing must use the same D-Bus connection as the BlueZ backend."""
        bus = SimpleNamespace(export=lambda *_a, **_k: None)
        client = SimpleNamespace(_backend=SimpleNamespace(_bus=bus), _bus=None)
        self.assertIs(bleak_message_bus(client), bus)


class PairTests(unittest.IsolatedAsyncioTestCase):
    async def test_pair_uses_client_pair_without_scanner_path(self) -> None:
        """Adding a PIN after diagnostic-only setup must still call Bleak pair()."""
        device = _device(MappingProxyType({"source": "local"}))
        client = SimpleNamespace(
            pair=AsyncMock(),
            _backend=SimpleNamespace(
                _device_path=BLUEZ_PATH,
                _bus=SimpleNamespace(export=lambda *_a, **_k: None),
            ),
        )
        with (
            patch(
                "custom_components.subzero_ble.pairing.async_device_is_paired",
                AsyncMock(return_value=False),
            ),
            patch(
                "custom_components.subzero_ble.pairing.PairingAgentSession.start",
                AsyncMock(),
            ),
            patch(
                "custom_components.subzero_ble.pairing.PairingAgentSession.stop",
                AsyncMock(),
            ),
        ):
            self.assertTrue(await async_pair_with_passkey(device, PIN, client=client))
        client.pair.assert_awaited_once()

    async def test_pair_without_path_or_client_pair_still_fails(self) -> None:
        """Keep a clear error when there is no BlueZ handle and no Bleak pair()."""
        device = _device({"source": "local"})
        with self.assertRaisesRegex(SubZeroPairingError, "device path is unavailable"):
            await async_pair_with_passkey(device, PIN, client=None)


if __name__ == "__main__":
    unittest.main()
