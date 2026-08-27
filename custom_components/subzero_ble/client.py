"""BLE client for communicating with Sub-Zero appliances."""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
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
    SUBSCRIBE_TIMEOUT,
)

if TYPE_CHECKING:
    from bleak.backends.device import BLEDevice

    from .coordinator import SubZeroData

_LOGGER = logging.getLogger(__name__)

OnPushCallback = Callable[["SubZeroData"], None]


class SubZeroCharacteristicMissing(BleakError):
    """Raised when the appliance GATT database is missing expected characteristics."""


class SubZeroBleClient:
    """Handles GATT connections, frame reassembly, and JSON parsing."""

    def __init__(
        self,
        ble_device: BLEDevice,
        on_push: OnPushCallback | None = None,
    ) -> None:
        """Initialize client."""
        self._ble_device = ble_device
        self._on_push = on_push
        self._buffer = bytearray()
        self._response_future: asyncio.Future[SubZeroData] | None = None
        self._lock = asyncio.Lock()
        self._client: BleakClientWithServiceCache | None = None
        self._poll_channel: BleakGATTCharacteristic | None = None
        self._subscribed: set[str] = set()
        self._closing = False

    def update_ble_device(self, ble_device: BLEDevice) -> None:
        """Update BLE device reference when HA discovers changes."""
        self._ble_device = ble_device

    def _disconnected(self, _client: BleakClientWithServiceCache) -> None:
        """Handle an unexpected disconnect from the appliance."""
        self._poll_channel = None
        self._subscribed.clear()
        if self._closing:
            return
        _LOGGER.warning(
            "Disconnected from Sub-Zero %s (%s)",
            self._ble_device.name,
            self._ble_device.address,
        )
        future = self._response_future
        if future and not future.done():
            future.get_loop().call_soon_threadsafe(
                _fail_future, future, BleakError("Sub-Zero disconnected during poll")
            )

    def _notification_handler(
        self, characteristic: BleakGATTCharacteristic, data: bytearray
    ) -> None:
        """Reassemble fragmented JSON indications by brace depth."""
        _LOGGER.debug(
            "Notify %s +%s bytes: %s",
            characteristic.uuid,
            len(data),
            _printable(data),
        )
        self._buffer.extend(data)
        if len(self._buffer) > MAX_FRAME_BYTES:
            _LOGGER.warning("Sub-Zero BLE reassembly buffer overflow; resetting")
            self._buffer.clear()
            return

        while True:
            complete = _pop_complete_json(self._buffer)
            if complete is None:
                return
            parsed = self._parse_payload(complete)
            future = self._response_future
            if future and not future.done():
                future.get_loop().call_soon_threadsafe(_resolve_future, future, parsed)
            elif self._on_push and parsed.has_values():
                _LOGGER.info(
                    "Sub-Zero push on %s: fridge_door=%s freezer_door=%s",
                    characteristic.uuid,
                    parsed.fridge_door_open,
                    parsed.freezer_door_open,
                )
                self._on_push(parsed)

    async def poll_state(self) -> SubZeroData:
        """Request status from the appliance, keeping the BLE connection open."""
        async with self._lock:
            await self._ensure_connected()
            assert self._client is not None
            assert self._poll_channel is not None
            channel = self._poll_channel

            loop = asyncio.get_running_loop()
            self._response_future = loop.create_future()
            self._buffer.clear()

            _LOGGER.info(
                "Polling get_async on %s (%s)",
                channel.uuid,
                self._ble_device.address,
            )
            await self._client.write_gatt_char(
                channel,
                GET_ASYNC_COMMAND,
                response="write" in channel.properties,
            )

            try:
                parsed = await asyncio.wait_for(
                    self._response_future, timeout=POLL_TIMEOUT
                )
            except TimeoutError as err:
                raise BleakError(
                    f"Timed out waiting for Sub-Zero response on {channel.uuid}"
                ) from err
            finally:
                self._response_future = None
            return parsed

    async def async_disconnect(self) -> None:
        """Drop the BLE connection if it is still open."""
        async with self._lock:
            self._closing = True
            client = self._client
            self._client = None
            self._poll_channel = None
            subscribed = list(self._subscribed)
            self._subscribed.clear()
            if client is None or not client.is_connected:
                return
            for uuid in subscribed:
                try:
                    await client.stop_notify(uuid)
                except BleakError:
                    _LOGGER.debug(
                        "Failed to stop notifications on %s", uuid, exc_info=True
                    )
            await client.disconnect()
            _LOGGER.info("Disconnected from Sub-Zero %s", self._ble_device.address)

    async def _ensure_connected(self) -> None:
        """Connect and subscribe if we are not already connected."""
        if self._client and self._client.is_connected and self._poll_channel:
            return

        self._closing = False

        _LOGGER.info(
            "Connecting to Sub-Zero %s (%s)",
            self._ble_device.name,
            self._ble_device.address,
        )
        self._client = await establish_connection(
            BleakClientWithServiceCache,
            self._ble_device,
            name=self._ble_device.name or self._ble_device.address,
            disconnected_callback=self._disconnected,
            max_attempts=3,
        )
        _log_gatt_table(self._client)
        self._poll_channel = _select_poll_channel(self._client)
        _LOGGER.info(
            "Using poll characteristic %s (properties=%s)",
            self._poll_channel.uuid,
            self._poll_channel.properties,
        )
        await self._subscribe(self._poll_channel)
        # D5/D6 are often visible before bonding on this firmware, but CCCD
        # subscribe/writes require encryption and can hang BlueZ indefinitely.
        if _characteristic_by_uuid(self._client, CHAR_D6_UUID):
            _LOGGER.info(
                "D6 is visible but requires BLE pairing for live door/temp "
                "pushes; polling D7 until pairing is implemented"
            )

    async def _subscribe(self, characteristic: BleakGATTCharacteristic) -> None:
        """Subscribe to indications/notifications on a characteristic."""
        uuid = str(characteristic.uuid).lower()
        if uuid in self._subscribed:
            return
        assert self._client is not None
        await asyncio.wait_for(
            self._client.start_notify(characteristic, self._notification_handler),
            timeout=SUBSCRIBE_TIMEOUT,
        )
        self._subscribed.add(uuid)
        _LOGGER.debug("Subscribed to %s", characteristic.uuid)

    def _parse_payload(self, raw_data: bytes) -> SubZeroData:
        """Parse raw JSON bytes into SubZeroData."""
        from .coordinator import SubZeroData

        printable = _printable(raw_data)
        try:
            payload: dict[str, Any] = json.loads(raw_data.decode("utf-8").strip())
        except (json.JSONDecodeError, UnicodeDecodeError) as err:
            _LOGGER.error("Failed to decode Sub-Zero payload %s: %s", printable, err)
            return SubZeroData()

        _LOGGER.debug("Received Sub-Zero JSON: %s", payload)

        if payload.get("status") not in (0, None):
            _LOGGER.warning(
                "Sub-Zero returned status %s (%s): %s",
                payload.get("status"),
                payload.get("status_msg"),
                printable,
            )
            return SubZeroData()

        fields = _extract_fields(payload)
        parsed = SubZeroData(
            fridge_temp=_optional_float(
                fields, "ref_set_temp", "refTemp", "fridge_temp"
            ),
            freezer_temp=_optional_float(
                fields, "frz_set_temp", "frzTemp", "freezer_temp"
            ),
            fridge_door_open=_optional_bool(
                fields, "ref_door_ajar", "refDoorOpen", "door_ajar"
            ),
            freezer_door_open=_optional_bool(
                fields, "frz_door_ajar", "frzDoorOpen"
            ),
            ice_maker_on=_optional_bool(fields, "ice_maker_on", "iceMakerEnabled"),
            water_filter_life=_optional_int(
                fields, "water_filter_pct_remaining", "waterFilterLife"
            ),
            air_filter_life=_optional_int(
                fields, "air_filter_pct_remaining", "airFilterLife"
            ),
        )
        _LOGGER.info(
            "Sub-Zero parsed %s: fridge_door=%s freezer_door=%s "
            "fridge_set=%s freezer_set=%s keys=%s",
            "push" if payload.get("msg_types") else "poll",
            parsed.fridge_door_open,
            parsed.freezer_door_open,
            parsed.fridge_temp,
            parsed.freezer_temp,
            sorted(fields),
        )
        if parsed.fridge_door_open is None and parsed.freezer_door_open is None:
            _LOGGER.info(
                "Door state was not in this payload. Raw JSON: %s", printable
            )
        return parsed


def _resolve_future(future: asyncio.Future[SubZeroData], result: SubZeroData) -> None:
    if not future.done():
        future.set_result(result)


def _fail_future(future: asyncio.Future[SubZeroData], err: Exception) -> None:
    if not future.done():
        future.set_exception(err)


def _optional_bool(fields: dict[str, Any], *keys: str) -> bool | None:
    for key in keys:
        if key in fields:
            return bool(fields[key])
    return None


def _optional_float(fields: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key in fields and fields[key] is not None:
            return float(fields[key])
    return None


def _optional_int(fields: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        if key in fields and fields[key] is not None:
            return int(fields[key])
    return None


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
        del buffer[: start]
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


def _printable(data: bytes | bytearray) -> str:
    """Return a log-safe ASCII preview of a BLE payload."""
    return "".join(chr(byte) if 32 <= byte <= 126 else "?" for byte in data)


def _log_gatt_table(client: BleakClientWithServiceCache) -> None:
    """Log the appliance GATT database to help diagnose pairing/channel issues."""
    for service in client.services:
        _LOGGER.info("GATT service %s", service.uuid)
        for char in service.characteristics:
            _LOGGER.info("  characteristic %s props=%s", char.uuid, char.properties)


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
        {
            str(char.uuid)
            for service in client.services
            for char in service.characteristics
        }
    )


def _select_poll_channel(
    client: BleakClientWithServiceCache,
) -> BleakGATTCharacteristic:
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
