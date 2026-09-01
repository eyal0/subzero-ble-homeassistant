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

from custom_components.subzero_ble.const import CHAR_D5_UUID, CHAR_D7_UUID

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


def _char(uuid: str) -> SimpleNamespace:
    return SimpleNamespace(
        uuid=uuid,
        properties=["read", "write", "indicate"],
        handle=1,
        descriptors=[],
    )


def _gatt_with_channels(*uuids: str) -> MagicMock:
    client = _connected_gatt_client()
    client.services = [
        SimpleNamespace(characteristics=[_char(uuid) for uuid in uuids])
    ]
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


class WriteDisplayPinTests(unittest.IsolatedAsyncioTestCase):
    def _client_with_channels(self, *uuids: str) -> SubZeroBleClient:
        client = SubZeroBleClient(_ble_device())
        client._client = _gatt_with_channels(*uuids)
        return client

    async def test_d7_accepted_skips_d5(self) -> None:
        """Unauthenticated D7 is enough; do not touch encrypted D5."""
        client = self._client_with_channels(CHAR_D7_UUID, CHAR_D5_UUID)
        with patch.object(
            client, "_try_display_pin_on_channel", new_callable=AsyncMock
        ) as mock_try:
            mock_try.return_value = "accepted"
            await client._write_display_pin(20)
        self.assertEqual(mock_try.await_count, 1)
        self.assertEqual(
            str(mock_try.await_args.args[0].uuid).lower(), CHAR_D7_UUID
        )

    async def test_d7_rejected_falls_through_to_d5(self) -> None:
        """If D7 does not accept the verb, try control channel D5."""
        client = self._client_with_channels(CHAR_D7_UUID, CHAR_D5_UUID)
        with patch.object(
            client, "_try_display_pin_on_channel", new_callable=AsyncMock
        ) as mock_try:
            mock_try.side_effect = ["rejected", "accepted"]
            await client._write_display_pin(20)
        self.assertEqual(mock_try.await_count, 2)
        self.assertEqual(
            [str(call.args[0].uuid).lower() for call in mock_try.await_args_list],
            [CHAR_D7_UUID, CHAR_D5_UUID],
        )

    async def test_d7_not_paired_falls_through_to_d5(self) -> None:
        """A D7 write error must not skip the D5 attempt."""
        client = self._client_with_channels(CHAR_D7_UUID, CHAR_D5_UUID)
        with patch.object(
            client, "_try_display_pin_on_channel", new_callable=AsyncMock
        ) as mock_try:
            mock_try.side_effect = [
                BleakError("[org.bluez.Error.NotPermitted] Not paired"),
                "accepted",
            ]
            await client._write_display_pin(20)
        self.assertEqual(mock_try.await_count, 2)

    async def test_missing_d7_uses_d5(self) -> None:
        """Fridge-pattern GATT without D7 still shows the PIN on D5."""
        client = self._client_with_channels(CHAR_D5_UUID)
        with patch.object(
            client, "_try_display_pin_on_channel", new_callable=AsyncMock
        ) as mock_try:
            mock_try.return_value = "accepted"
            await client._write_display_pin(20)
        self.assertEqual(mock_try.await_count, 1)
        self.assertEqual(
            str(mock_try.await_args.args[0].uuid).lower(), CHAR_D5_UUID
        )

    async def test_d7_no_ack_then_d5_not_paired_raises(self) -> None:
        """IT30CI unpaired: D7 silent, D5 Not paired — raise the D5 error."""
        client = self._client_with_channels(CHAR_D7_UUID, CHAR_D5_UUID)
        not_paired = BleakError("[org.bluez.Error.NotPermitted] Not paired")
        with patch.object(
            client, "_try_display_pin_on_channel", new_callable=AsyncMock
        ) as mock_try:
            mock_try.side_effect = ["wrote", not_paired]
            with self.assertRaisesRegex(BleakError, "Not paired"):
                await client._write_display_pin(20)

    async def test_neither_channel_in_gatt_table(self) -> None:
        client = self._client_with_channels()
        with self.assertRaisesRegex(BleakError, "D7 and D5 are not in the GATT table"):
            await client._write_display_pin(20)
