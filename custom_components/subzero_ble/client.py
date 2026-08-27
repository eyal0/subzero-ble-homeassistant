"""BLE Client for communicating with Sub-Zero appliances."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from bleak import BleakClient, BleakGATTCharacteristic
from bleak_retry_connector import establish_connection

if TYPE_CHECKING:
    from bleak.backends.device import BLEDevice
    from .coordinator import SubZeroData

_LOGGER = logging.getLogger(__name__)

# Sub-Zero UART / Data Transfer GATT Service & Characteristic UUIDs
SUBZERO_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
SUBZERO_RX_CHAR_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # Write to appliance
SUBZERO_TX_CHAR_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # Notify from appliance

# Commands
POLL_STATE_COMMAND = b'{"cmd":"getStatus"}\n'


class SubZeroBleClient:
    """Handles GATT connections, frame reassembly, and JSON parsing."""

    def __init__(self, ble_device: BLEDevice) -> None:
        """Initialize client."""
        self._ble_device = ble_device
        self._buffer = bytearray()
        self._response_future: asyncio.Future[bytes] | None = None
        self._lock = asyncio.Lock()

    def update_ble_device(self, ble_device: BLEDevice) -> None:
        """Update BLE device reference when HA discovers changes."""
        self._ble_device = ble_device

    def _notification_handler(
        self, characteristic: BleakGATTCharacteristic, data: bytearray
    ) -> None:
        """Handle incoming chunked frames and reassemble them."""
        self._buffer.extend(data)

        # Packets terminate with newline or closing JSON delimiter
        if b"\n" in self._buffer or (
            self._buffer.startswith(b"{") and self._buffer.endswith(b"}")
        ):
            if self._response_future and not self._response_future.done():
                self._response_future.set_result(bytes(self._buffer))
            self._buffer.clear()

    async def poll_state(self) -> SubZeroData:
        """Connect to appliance, request status, and parse readings."""
        from .coordinator import SubZeroData

        async with self._lock:
            client = await establish_connection(
                BleakClient,
                self._ble_device,
                name=self._ble_device.name or self._ble_device.address,
                max_attempts=3,
            )

            try:
                loop = asyncio.get_running_loop()
                self._response_future = loop.create_future()
                self._buffer.clear()

                # Start notification listener on TX
                await client.start_notify(
                    SUBZERO_TX_CHAR_UUID, self._notification_handler
                )

                # Send getStatus command to RX
                await client.write_gatt_char(
                    SUBZERO_RX_CHAR_UUID, POLL_STATE_COMMAND, response=False
                )

                # Wait for reassembled response frame (timeout after 10s)
                raw_response = await asyncio.wait_for(
                    self._response_future, timeout=10.0
                )
                await client.stop_notify(SUBZERO_TX_CHAR_UUID)

                return self._parse_payload(raw_response)

            finally:
                if client.is_connected:
                    await client.disconnect()

    def _parse_payload(self, raw_data: bytes) -> SubZeroData:
        """Parse raw JSON string into SubZeroData model."""
        from .coordinator import SubZeroData

        try:
            payload: dict[str, Any] = json.loads(raw_data.decode("utf-8").strip())
        except (json.JSONDecodeError, UnicodeDecodeError) as err:
            _LOGGER.error("Failed to decode Sub-Zero payload '%s': %s", raw_data, err)
            return SubZeroData()

        _LOGGER.debug("Received Sub-Zero payload: %s", payload)

        # Extract telemetry keys (mapping Sub-Zero protocol values)
        fridge_temp = payload.get("refTemp") or payload.get("fridge_temp")
        freezer_temp = payload.get("frzTemp") or payload.get("freezer_temp")
        fridge_door = bool(payload.get("refDoorOpen", False))
        freezer_door = bool(payload.get("frzDoorOpen", False))
        ice_maker = bool(payload.get("iceMakerEnabled", False))
        water_filter = payload.get("waterFilterLife")
        air_filter = payload.get("airFilterLife")

        return SubZeroData(
            fridge_temp=float(fridge_temp) if fridge_temp is not None else None,
            freezer_temp=float(freezer_temp) if freezer_temp is not None else None,
            fridge_door_open=fridge_door,
            freezer_door_open=freezer_door,
            ice_maker_on=ice_maker,
            water_filter_life=int(water_filter) if water_filter is not None else None,
            air_filter_life=int(air_filter) if air_filter is not None else None,
        )