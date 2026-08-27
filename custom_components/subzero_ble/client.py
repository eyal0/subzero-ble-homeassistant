"""BLE client for communicating with Sub-Zero appliances."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from bleak import BleakGATTCharacteristic
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from .const import (
    CHAR_D6_UUID,
    CHAR_D7_UUID,
    GET_ASYNC_COMMAND,
    MAX_FRAME_BYTES,
    POLL_TIMEOUT,
)

if TYPE_CHECKING:
    from bleak.backends.device import BLEDevice

    from .coordinator import SubZeroData

_LOGGER = logging.getLogger(__name__)


class SubZeroCharacteristicMissing(BleakError):
    """Raised when the appliance GATT database is missing expected characteristics."""


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
        """Reassemble fragmented JSON indications by brace depth."""
        self._buffer.extend(data)
        if len(self._buffer) > MAX_FRAME_BYTES:
            _LOGGER.warning("Sub-Zero BLE reassembly buffer overflow; resetting")
            self._buffer.clear()
            return

        complete = _pop_complete_json(self._buffer)
        if complete is None:
            return
        if self._response_future and not self._response_future.done():
            self._response_future.set_result(complete)

    async def poll_state(self) -> SubZeroData:
        """Connect to the appliance, request status, and parse readings."""
        async with self._lock:
            client = await establish_connection(
                BleakClientWithServiceCache,
                self._ble_device,
                name=self._ble_device.name or self._ble_device.address,
                max_attempts=3,
            )

            notify_started = False
            try:
                channel = _select_poll_channel(client)
                _LOGGER.debug(
                    "Polling Sub-Zero %s on characteristic %s",
                    self._ble_device.address,
                    channel.uuid,
                )

                loop = asyncio.get_running_loop()
                self._response_future = loop.create_future()
                self._buffer.clear()

                await client.start_notify(channel, self._notification_handler)
                notify_started = True
                await client.write_gatt_char(
                    channel,
                    GET_ASYNC_COMMAND,
                    response="write" in channel.properties,
                )

                try:
                    raw_response = await asyncio.wait_for(
                        self._response_future, timeout=POLL_TIMEOUT
                    )
                except TimeoutError as err:
                    raise BleakError(
                        f"Timed out waiting for Sub-Zero response on {channel.uuid}"
                    ) from err
                return self._parse_payload(raw_response)
            finally:
                if notify_started:
                    try:
                        await client.stop_notify(channel)
                    except BleakError:
                        _LOGGER.debug(
                            "Failed to stop Sub-Zero notifications", exc_info=True
                        )
                self._response_future = None
                if client.is_connected:
                    await client.disconnect()

    def _parse_payload(self, raw_data: bytes) -> SubZeroData:
        """Parse raw JSON bytes into SubZeroData."""
        from .coordinator import SubZeroData

        try:
            payload: dict[str, Any] = json.loads(raw_data.decode("utf-8").strip())
        except (json.JSONDecodeError, UnicodeDecodeError) as err:
            _LOGGER.error("Failed to decode Sub-Zero payload %r: %s", raw_data, err)
            return SubZeroData()

        _LOGGER.debug("Received Sub-Zero payload: %s", payload)

        if payload.get("status") not in (0, None):
            _LOGGER.warning(
                "Sub-Zero returned status %s (%s)",
                payload.get("status"),
                payload.get("status_msg"),
            )
            return SubZeroData()

        fields = _extract_fields(payload)
        fridge_temp = fields.get("ref_set_temp", fields.get("refTemp"))
        freezer_temp = fields.get("frz_set_temp", fields.get("frzTemp"))
        water_filter = fields.get(
            "water_filter_pct_remaining", fields.get("waterFilterLife")
        )
        air_filter = fields.get(
            "air_filter_pct_remaining", fields.get("airFilterLife")
        )
        fridge_door = fields.get("ref_door_ajar", fields.get("refDoorOpen"))
        freezer_door = fields.get("frz_door_ajar", fields.get("frzDoorOpen"))
        ice_maker = fields.get("ice_maker_on", fields.get("iceMakerEnabled"))

        return SubZeroData(
            fridge_temp=float(fridge_temp) if fridge_temp is not None else None,
            freezer_temp=float(freezer_temp) if freezer_temp is not None else None,
            fridge_door_open=bool(fridge_door) if fridge_door is not None else False,
            freezer_door_open=bool(freezer_door) if freezer_door is not None else False,
            ice_maker_on=bool(ice_maker) if ice_maker is not None else False,
            water_filter_life=int(water_filter) if water_filter is not None else None,
            air_filter_life=int(air_filter) if air_filter is not None else None,
        )


def _extract_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Unwrap resp/props envelopes used by poll and push notifications."""
    resp = payload.get("resp")
    if isinstance(resp, dict) and resp:
        return resp
    props = payload.get("props")
    if isinstance(props, dict) and props:
        return props
    return payload


def _pop_complete_json(buffer: bytearray) -> bytes | None:
    """Return the first complete JSON object from buffer, if present."""
    try:
        text = buffer.decode("utf-8")
    except UnicodeDecodeError:
        return None

    start = text.find("{")
    if start == -1:
        buffer.clear()
        return None
    if start:
        del buffer[:start]
        text = text[start:]

    depth = 0
    for index, char in enumerate(text):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                complete = text[: index + 1]
                remainder = text[index + 1 :]
                buffer.clear()
                if remainder:
                    buffer.extend(remainder.encode("utf-8"))
                return complete.encode("utf-8")
    return None


def _characteristic_by_uuid(
    client: BleakClientWithServiceCache, uuid: str
) -> BleakGATTCharacteristic | None:
    """Return a characteristic by UUID, or None if it is not in the GATT DB."""
    target = uuid.lower()
    for service in client.services:
        for char in service.characteristics:
            if str(char.uuid).lower() == target:
                return char
    return None


def _discovered_characteristic_uuids(client: BleakClientWithServiceCache) -> list[str]:
    """Return sorted characteristic UUIDs currently visible on the appliance."""
    return sorted(
        {str(char.uuid) for service in client.services for char in service.characteristics}
    )


def _select_poll_channel(client: BleakClientWithServiceCache) -> BleakGATTCharacteristic:
    """Pick a poll characteristic that does not require bonding.

    D7 is the open pre-auth diagnostic channel and is visible before pairing.
    D6 is the full-state channel, but it only appears after bonding and needs
    an unlock_channel PIN session.
    """
    if char := _characteristic_by_uuid(client, CHAR_D7_UUID):
        return char
    if char := _characteristic_by_uuid(client, CHAR_D6_UUID):
        return char

    found = _discovered_characteristic_uuids(client)
    raise SubZeroCharacteristicMissing(
        "Sub-Zero data characteristic was not found. "
        f"Discovered characteristics: {found}"
    )
