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
    CHAR_D5_UUID,
    CHAR_D6_UUID,
    CHAR_D7_UUID,
    CONNECT_TIMEOUT,
    GET_ASYNC_COMMAND,
    GET_COMMAND,
    MAX_FRAME_BYTES,
    POLL_TIMEOUT,
    SUBSCRIBE_TIMEOUT,
    UNLOCK_TIMEOUT,
    display_pin_command,
    unlock_command,
)
from .pairing import SubZeroPairingError, async_pair_with_passkey

if TYPE_CHECKING:
    from bleak.backends.device import BLEDevice

    from .coordinator import SubZeroData

_LOGGER = logging.getLogger(__name__)
_BLE_LOG = logging.getLogger("custom_components.subzero_ble.ble")

OnPushCallback = Callable[["SubZeroData"], None]


class SubZeroCharacteristicMissing(BleakError):
    """Raised when the appliance GATT database is missing expected characteristics."""


class SubZeroInvalidPin(BleakError):
    """Raised when the appliance rejects unlock_channel (status 302)."""


class SubZeroBleClient:
    """Handles GATT connections, frame reassembly, and JSON parsing."""

    def __init__(
        self,
        ble_device: BLEDevice,
        on_push: OnPushCallback | None = None,
        pin: str | None = None,
    ) -> None:
        """Initialize client."""
        self._ble_device = ble_device
        self._on_push = on_push
        self._pin = pin
        self._buffers: dict[str, bytearray] = {}
        self._response_future: asyncio.Future[bytes] | None = None
        self._expect_uuid: str | None = None
        self._lock = asyncio.Lock()
        self._client: BleakClientWithServiceCache | None = None
        self._poll_channel: BleakGATTCharacteristic | None = None
        self._subscribed: set[str] = set()
        self._closing = False
        self._unlocked = False
        self._use_get_verb = False

    def update_ble_device(self, ble_device: BLEDevice) -> None:
        """Update BLE device reference when HA discovers changes."""
        self._ble_device = ble_device

    def update_pin(self, pin: str | None) -> bool:
        """Update the pairing PIN. Return True if a reconnect is required."""
        if pin == self._pin:
            return False
        self._pin = pin
        self._unlocked = False
        return True

    def _disconnected(self, _client: BleakClientWithServiceCache) -> None:
        """Handle an unexpected disconnect from the appliance."""
        self._poll_channel = None
        self._subscribed.clear()
        self._unlocked = False
        self._buffers.clear()
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
        _log_ble_pdu("RX", characteristic, data)
        uuid = str(characteristic.uuid).lower()
        buffer = self._buffers.setdefault(uuid, bytearray())
        # A new JSON frame starting with `{` while the buffer is still
        # unbalanced means a prior indication was truncated (BLE fragment
        # drop). Discard the partial message rather than splicing.
        if data and data[0:1] == b"{" and buffer:
            try:
                pending = buffer.decode("utf-8")
            except UnicodeDecodeError:
                pending = ""
            if _json_brace_depth(pending) != 0:
                _LOGGER.debug(
                    "Discarding %s bytes of truncated JSON before new frame",
                    len(buffer),
                )
                buffer.clear()

        buffer.extend(data)
        if len(buffer) > MAX_FRAME_BYTES:
            _LOGGER.warning("Sub-Zero BLE reassembly buffer overflow; resetting")
            buffer.clear()
            return

        while True:
            complete = _pop_complete_json(buffer)
            if complete is None:
                return
            _BLE_LOG.debug(
                "Reassembled %s-byte JSON: %s",
                len(complete),
                _printable(complete),
            )
            future = self._response_future
            if (
                future
                and not future.done()
                and (self._expect_uuid is None or uuid == self._expect_uuid)
            ):
                future.get_loop().call_soon_threadsafe(_resolve_future, future, complete)
                continue
            parsed = self._parse_payload(complete)
            if self._on_push and parsed.has_values():
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
            assert self._poll_channel is not None
            channel = self._poll_channel
            command = GET_COMMAND if self._use_get_verb else GET_ASYNC_COMMAND
            raw = await self._write_and_wait(channel, command, timeout=POLL_TIMEOUT)
            payload = _loads_json(raw)
            if (
                payload is not None
                and payload.get("status") == 1
                and payload.get("resp") == {}
                and not self._use_get_verb
            ):
                _LOGGER.info("get_async returned empty state; retrying with get")
                self._use_get_verb = True
                raw = await self._write_and_wait(
                    channel, GET_COMMAND, timeout=POLL_TIMEOUT
                )
            return self._parse_payload(raw)

    async def async_display_pin(self, duration: int = 30) -> None:
        """Ask the appliance to show its PIN on the display for `duration` seconds."""
        async with self._lock:
            await self._ensure_connected()
            assert self._client is not None
            channel = _characteristic_by_uuid(self._client, CHAR_D5_UUID)
            if channel is None:
                raise BleakError("D5 is not available; pair the appliance first")
            await self._subscribe_optional(channel)
            await self._write_and_wait(
                channel, display_pin_command(duration), timeout=UNLOCK_TIMEOUT
            )

    async def async_disconnect(self) -> None:
        """Drop the BLE connection if it is still open."""
        async with self._lock:
            self._closing = True
            client = self._client
            self._client = None
            self._poll_channel = None
            self._unlocked = False
            subscribed = list(self._subscribed)
            self._subscribed.clear()
            if client is None or not client.is_connected:
                return
            for uuid in subscribed:
                try:
                    _BLE_LOG.debug("CCCD unsubscribe %s", uuid)
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
            if self._pin and not self._unlocked:
                d5 = _characteristic_by_uuid(self._client, CHAR_D5_UUID)
                d6 = _characteristic_by_uuid(self._client, CHAR_D6_UUID)
                await self._unlock_channels(d5, d6)
                if d6 is not None and str(d6.uuid).lower() in self._subscribed:
                    self._poll_channel = d6
            return

        self._closing = False

        _LOGGER.info(
            "Connecting to Sub-Zero %s (%s)",
            self._ble_device.name,
            self._ble_device.address,
        )
        self._client = await asyncio.wait_for(
            establish_connection(
                BleakClientWithServiceCache,
                self._ble_device,
                name=self._ble_device.name or self._ble_device.address,
                disconnected_callback=self._disconnected,
                max_attempts=3,
            ),
            timeout=CONNECT_TIMEOUT,
        )
        _log_gatt_table(self._client)
        _BLE_LOG.debug(
            "Connected to %s mtu=%s rssi=%s",
            self._ble_device.address,
            getattr(self._client, "mtu_size", None),
            getattr(self._ble_device, "rssi", None),
        )
        if self._pin:
            try:
                await async_pair_with_passkey(self._ble_device, self._pin)
            except SubZeroPairingError as err:
                _LOGGER.warning("BLE pairing did not complete: %s", err)

        d7 = _characteristic_by_uuid(self._client, CHAR_D7_UUID)
        d6 = _characteristic_by_uuid(self._client, CHAR_D6_UUID)
        d5 = _characteristic_by_uuid(self._client, CHAR_D5_UUID)
        if d7 is not None:
            await self._subscribe(d7)

        if self._pin:
            if d5 is not None:
                await self._subscribe_optional(d5)
            if d6 is not None:
                await self._subscribe_optional(d6)
            await self._unlock_channels(d5, d6)
            if d6 is not None and str(d6.uuid).lower() in self._subscribed:
                self._poll_channel = d6
                _LOGGER.info("Using D6 for full-state polling after unlock")
            elif d7 is not None:
                self._poll_channel = d7
                _LOGGER.info("Unlock attempted; falling back to D7 polling")
            else:
                self._poll_channel = _select_poll_channel(self._client)
        else:
            self._poll_channel = d7 or _select_poll_channel(self._client)
            if d6 is not None:
                _LOGGER.info(
                    "D6 is visible but no PIN is configured; polling D7. "
                    "Add the 6-digit appliance PIN in the integration options."
                )

        _LOGGER.info(
            "Using poll characteristic %s (properties=%s)",
            self._poll_channel.uuid,
            self._poll_channel.properties,
        )
        await self._subscribe(self._poll_channel)

    async def _unlock_channels(
        self,
        d5: BleakGATTCharacteristic | None,
        d6: BleakGATTCharacteristic | None,
    ) -> None:
        """Send unlock_channel on D5 and D6 (each channel needs its own unlock)."""
        assert self._pin is not None
        command = unlock_command(self._pin)
        unlocked_any = False
        for channel in (d5, d6):
            if channel is None or str(channel.uuid).lower() not in self._subscribed:
                continue
            name = _channel_name(channel)
            _LOGGER.info("Sending unlock_channel on %s", name)
            raw = await self._write_and_wait(
                channel, command, timeout=UNLOCK_TIMEOUT, redact=True
            )
            payload = _loads_json(raw)
            status = None if payload is None else payload.get("status")
            if status == 302:
                raise SubZeroInvalidPin(
                    "Appliance rejected PIN (status 302). "
                    "The code may have rotated — check the display."
                )
            if status not in (0, None):
                _LOGGER.warning(
                    "unlock_channel on %s returned status %s", name, status
                )
                continue
            _LOGGER.info("Unlocked %s", name)
            unlocked_any = True
        self._unlocked = unlocked_any

    async def _write_and_wait(
        self,
        channel: BleakGATTCharacteristic,
        data: bytes,
        timeout: float,
        redact: bool = False,
    ) -> bytes:
        """Write a command and wait for a JSON indication on that characteristic."""
        assert self._client is not None
        uuid = str(channel.uuid).lower()
        self._buffers.setdefault(uuid, bytearray()).clear()
        loop = asyncio.get_running_loop()
        self._response_future = loop.create_future()
        self._expect_uuid = uuid
        with_response = "write" in channel.properties
        _log_ble_pdu(
            "TX",
            channel,
            data,
            extra=f"write_with_response={with_response}",
            redact=redact,
        )
        try:
            await self._client.write_gatt_char(
                channel, data, response=with_response
            )
            return await asyncio.wait_for(self._response_future, timeout=timeout)
        except TimeoutError as err:
            raise BleakError(
                f"Timed out waiting for Sub-Zero response on {channel.uuid}"
            ) from err
        finally:
            self._response_future = None
            self._expect_uuid = None

    async def _subscribe_optional(self, characteristic: BleakGATTCharacteristic) -> None:
        """Subscribe, ignoring auth/timeout failures on encrypted characteristics."""
        try:
            await self._subscribe(characteristic)
        except (BleakError, TimeoutError) as err:
            _LOGGER.info(
                "Could not subscribe to %s (encryption/pairing may be required): %s",
                _channel_name(characteristic),
                err,
            )

    async def _subscribe(self, characteristic: BleakGATTCharacteristic) -> None:
        """Subscribe to indications/notifications on a characteristic."""
        uuid = str(characteristic.uuid).lower()
        if uuid in self._subscribed:
            return
        assert self._client is not None
        _BLE_LOG.debug(
            "CCCD subscribe %s handle=%s props=%s",
            _channel_name(characteristic),
            getattr(characteristic, "handle", None),
            characteristic.properties,
        )
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
        payload = _loads_json(raw_data)
        if payload is None:
            _LOGGER.error("Failed to decode Sub-Zero payload %s", printable)
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


def _resolve_future(future: asyncio.Future[bytes], result: bytes) -> None:
    if not future.done():
        future.set_result(result)


def _fail_future(future: asyncio.Future[bytes], err: Exception) -> None:
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
        del buffer[:start]
        text = text[start:]

    complete = _slice_complete_json(text)
    if complete is None:
        return None
    remainder = text[len(complete) :]
    buffer.clear()
    if remainder:
        buffer.extend(remainder.encode("utf-8"))
    return complete.encode("utf-8")


def _slice_complete_json(text: str) -> str | None:
    """Return the leading complete JSON object, ignoring braces inside strings."""
    depth = 0
    in_string = False
    escape = False
    for index, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[: index + 1]
    return None


def _json_brace_depth(text: str) -> int:
    """Return net JSON object depth, ignoring braces inside strings."""
    depth = 0
    in_string = False
    escape = False
    for char in text:
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
    return depth


def _loads_json(raw_data: bytes) -> dict[str, Any] | None:
    """Parse JSON, recovering a valid object if two frames were spliced."""
    try:
        text = raw_data.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = _recover_json_object(text)
        if payload is not None:
            _LOGGER.debug("Recovered JSON object from spliced BLE payload")
    if isinstance(payload, dict):
        return payload
    return None


def _recover_json_object(text: str) -> dict[str, Any] | None:
    """Find a parseable JSON object in a spliced BLE buffer."""
    start = 0
    while True:
        idx = text.find("{", start)
        if idx == -1:
            return None
        extracted = _slice_complete_json(text[idx:])
        if extracted:
            try:
                payload = json.loads(extracted)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict) and (
                payload.get("status") == 0 or "resp" in payload or "props" in payload
            ):
                return payload
        start = idx + 1


def _printable(data: bytes | bytearray) -> str:
    """Return a log-safe ASCII preview of a BLE payload."""
    return "".join(chr(byte) if 32 <= byte <= 126 else "?" for byte in data)


def _hex(data: bytes | bytearray) -> str:
    """Return space-separated hex suitable for comparison with ESPHome logs."""
    return bytes(data).hex(" ")


def _channel_name(characteristic: BleakGATTCharacteristic | str) -> str:
    """Return D4–D8 when the UUID matches a Sub-Zero characteristic."""
    uuid = (
        str(characteristic.uuid)
        if isinstance(characteristic, BleakGATTCharacteristic)
        else str(characteristic)
    ).lower()
    suffix = uuid.replace("-", "")[-2:]
    if suffix in {"d4", "d5", "d6", "d7", "d8"}:
        return suffix.upper()
    return uuid


def _log_ble_pdu(
    direction: str,
    characteristic: BleakGATTCharacteristic,
    data: bytes | bytearray,
    extra: str = "",
    redact: bool = False,
) -> None:
    """Log a BLE write or notification at protocol-trace level."""
    suffix = f" {extra}" if extra else ""
    ascii_preview = "<redacted>" if redact else _printable(data)
    hex_preview = "<redacted>" if redact else _hex(data)
    _BLE_LOG.debug(
        "%s %s handle=%s %s bytes ascii=%s hex=%s%s",
        direction,
        _channel_name(characteristic),
        getattr(characteristic, "handle", None),
        len(data),
        ascii_preview,
        hex_preview,
        suffix,
    )


def _log_gatt_table(client: BleakClientWithServiceCache) -> None:
    """Log the appliance GATT database to help diagnose pairing/channel issues."""
    for service in client.services:
        _LOGGER.info("GATT service %s", service.uuid)
        _BLE_LOG.debug("GATT service %s", service.uuid)
        for char in service.characteristics:
            _LOGGER.info(
                "  characteristic %s handle=%s props=%s",
                char.uuid,
                getattr(char, "handle", None),
                char.properties,
            )
            _BLE_LOG.debug(
                "  %s uuid=%s handle=%s props=%s",
                _channel_name(char),
                char.uuid,
                getattr(char, "handle", None),
                char.properties,
            )
            for descriptor in char.descriptors:
                _BLE_LOG.debug(
                    "    descriptor %s handle=%s",
                    descriptor.uuid,
                    getattr(descriptor, "handle", None),
                )


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
