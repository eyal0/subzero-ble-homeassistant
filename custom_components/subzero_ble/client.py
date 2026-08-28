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
    CHAR_D4_UUID,
    CHAR_D5_UUID,
    CHAR_D6_UUID,
    CHAR_D7_UUID,
    CHAR_D8_UUID,
    CONNECT_TIMEOUT,
    CONNECTION_CONNECTED,
    CONNECTION_DIAGNOSTIC,
    CONNECTION_DISCONNECTED,
    CONNECTION_PAIRED,
    DISPLAY_PIN_ACK_TIMEOUT,
    DISPLAY_PIN_DURATION,
    DISPLAY_PIN_RETRY_SECONDS,
    DISPLAY_PIN_RETRY_TIMEOUT,
    GET_ASYNC_COMMAND,
    GET_COMMAND,
    LINK_SETTLE_SECONDS,
    MAX_FRAME_BYTES,
    PAIR_TIMEOUT,
    POLL_TIMEOUT,
    RECONNECT_GAP_SECONDS,
    SET_WRITE_GAP_SECONDS,
    SUBSCRIBE_TIMEOUT,
    UNLOCK_TIMEOUT,
    display_pin_command,
    set_command,
    unlock_command,
)
from .pairing import (
    PairingAgentSession,
    SubZeroPairingError,
    async_device_is_paired,
    async_pair_with_passkey,
    bleak_message_bus,
)

if TYPE_CHECKING:
    from bleak.backends.device import BLEDevice

    from .coordinator import SubZeroData

_LOGGER = logging.getLogger(__name__)
_BLE_LOG = logging.getLogger("custom_components.subzero_ble.ble")

OnPushCallback = Callable[["SubZeroData"], None]
OnDisconnectCallback = Callable[[], None]


class SubZeroCharacteristicMissing(BleakError):
    """Raised when the appliance GATT database is missing expected characteristics."""


class SubZeroInvalidPin(BleakError):
    """Raised when the appliance rejects unlock_channel (status 3 or 302)."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        lockout_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.lockout_seconds = lockout_seconds


class SubZeroBleClient:
    """Handles GATT connections, newline-delimited JSON reassembly, and parsing."""

    def __init__(
        self,
        ble_device: BLEDevice,
        on_push: OnPushCallback | None = None,
        on_disconnect: OnDisconnectCallback | None = None,
        pin: str | None = None,
    ) -> None:
        """Initialize client."""
        self._ble_device = ble_device
        self._on_push = on_push
        self._on_disconnect = on_disconnect
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
        self._pairing: PairingAgentSession | None = None

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

    def connection_status(self) -> str:
        """Return a short BLE/pairing status for Home Assistant."""
        if self._client is None or not self._client.is_connected:
            return CONNECTION_DISCONNECTED
        if self._unlocked:
            return CONNECTION_PAIRED
        if not self._pin:
            return CONNECTION_DIAGNOSTIC
        return CONNECTION_CONNECTED

    def _disconnected(self, client: BleakClientWithServiceCache) -> None:
        """Handle an unexpected disconnect from the appliance."""
        # Pair() is followed by a reconnect so ATT uses the bond. The first
        # client's callback can fire after the second client is already live.
        if client is not self._client:
            _BLE_LOG.debug(
                "Ignoring disconnect from a replaced GATT client for %s",
                self._ble_device.address,
            )
            return
        self._poll_channel = None
        self._subscribed.clear()
        self._unlocked = False
        self._buffers.clear()
        if self._closing:
            return
        pairing = self._pairing
        self._pairing = None
        self._client = None
        if pairing is not None:
            try:
                asyncio.get_running_loop().create_task(pairing.stop())
            except RuntimeError:
                pass
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
        if self._on_disconnect:
            self._on_disconnect()

    def _notification_handler(
        self, characteristic: BleakGATTCharacteristic, data: bytearray
    ) -> None:
        """Reassemble fragmented JSON indications until a trailing newline."""
        _log_ble_pdu("RX", characteristic, data)
        uuid = str(characteristic.uuid).lower()
        buffer = self._buffers.setdefault(uuid, bytearray())
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
            try:
                return await self._poll_once()
            except BleakError as err:
                if not _is_link_drop(err):
                    raise
                _LOGGER.warning(
                    "BLE link dropped during poll; reconnecting and retrying: %s",
                    err,
                )
                await self._disconnect_unlocked()
                return await self._poll_once()

    async def _poll_once(self) -> SubZeroData:
        """One get_async/get cycle. Caller must hold the client lock."""
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

    async def async_display_pin(
        self, duration: int = DISPLAY_PIN_DURATION
    ) -> None:
        """Ask the appliance to show its PIN on D5, retrying until it accepts.

        The fridge may reject display_pin when the door is closed; retry every
        few seconds so the user can open a door.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + DISPLAY_PIN_RETRY_TIMEOUT
        while True:
            try:
                async with self._lock:
                    await self._ensure_connected(require_pair=False)
                    try:
                        await self._write_display_pin(duration)
                        return
                    except BleakError as err:
                        if not _is_insufficient_auth(err) or not self._pin:
                            raise
                        _LOGGER.info(
                            "display_pin hit insufficient authentication; "
                            "re-pairing and reconnecting"
                        )
                        await self._reconnect_encrypted(force_pair=True)
                        await self._write_display_pin(duration)
                        return
            except BleakError as err:
                if not _is_display_pin_retryable(err):
                    raise
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise BleakError(
                        f"{err} Open a refrigerator or freezer door and "
                        "press Show PIN again."
                    ) from err
                _LOGGER.info(
                    "display_pin failed (%s); retrying in %ss (open a door)",
                    err,
                    DISPLAY_PIN_RETRY_SECONDS,
                )
                await asyncio.sleep(min(DISPLAY_PIN_RETRY_SECONDS, remaining))

    async def _write_display_pin(self, duration: int) -> None:
        """Write display_pin on D5."""
        assert self._client is not None
        channel = _characteristic_by_uuid(self._client, CHAR_D5_UUID)
        if channel is None:
            raise BleakError("D5 is not in the GATT table")
        result = await self._try_display_pin_on_channel(
            channel, display_pin_command(duration), duration
        )
        if result == "accepted":
            _LOGGER.info("display_pin accepted on D5; watch the appliance display")
            return
        if result == "rejected":
            raise BleakError("display_pin was not accepted on D5")
        raise BleakError("display_pin was written to D5 but no status 0 ack")

    async def _try_display_pin_on_channel(
        self,
        channel: BleakGATTCharacteristic,
        command: bytes,
        duration: int,
    ) -> str:
        """Send display_pin on one characteristic. Return accepted/rejected/wrote."""
        name = _channel_name(channel)
        await self._subscribe_optional(channel)
        _LOGGER.info("Sending display_pin on %s for %s seconds", name, duration)
        subscribed = str(channel.uuid).lower() in self._subscribed
        if subscribed:
            try:
                raw = await self._write_and_wait(
                    channel, command, timeout=DISPLAY_PIN_ACK_TIMEOUT
                )
            except BleakError as err:
                if "timed out" in str(err).lower():
                    _LOGGER.info("display_pin written to %s; no JSON ack", name)
                    return "wrote"
                raise
            payload = _loads_json(raw)
            if payload is None:
                _LOGGER.info("display_pin on %s got a non-JSON response", name)
                return "wrote"
            status = payload.get("status")
            if status == 0:
                return "accepted"
            if status is None:
                return "wrote"
            _LOGGER.info("display_pin on %s returned status %s", name, status)
            return "rejected"
        assert self._client is not None
        with_response = "write" in channel.properties
        _log_ble_pdu(
            "TX",
            channel,
            command,
            extra=f"write_with_response={with_response}",
        )
        await self._client.write_gatt_char(
            channel, command, response=with_response
        )
        _LOGGER.info(
            "display_pin written to %s without a notification subscription",
            name,
        )
        return "wrote"

    async def async_set_property(self, key: str, value: object) -> None:
        """Write one property on the encrypted D5 control channel."""
        if not self._pin:
            raise BleakError(
                "Enter the 6-digit PIN under Configure first. "
                "Writes use encrypted channel D5."
            )
        async with self._lock:
            await self._ensure_connected(require_pair=True)
            await self._write_set(key, value)

    async def async_set_properties(self, params: dict[str, object]) -> None:
        """Write several properties on D5, spaced so the appliance keeps each set."""
        if not self._pin:
            raise BleakError(
                "Enter the 6-digit PIN under Configure first. "
                "Writes use encrypted channel D5."
            )
        if not params:
            return
        async with self._lock:
            await self._ensure_connected(require_pair=True)
            first = True
            for key, value in params.items():
                if not first:
                    await asyncio.sleep(SET_WRITE_GAP_SECONDS)
                first = False
                await self._write_set(key, value)

    async def _write_set(self, key: str, value: object) -> None:
        """Send `set` on D5 and wait for an ack."""
        assert self._client is not None
        channel = _characteristic_by_uuid(self._client, CHAR_D5_UUID)
        if channel is None:
            raise BleakError(
                "D5 is not available. Pairing may not have completed — "
                "check the PIN and watch the appliance display."
            )
        await self._subscribe_optional(channel)
        if str(channel.uuid).lower() not in self._subscribed:
            raise BleakError(
                "Could not subscribe to D5. The adapter is not bonded yet."
            )
        if not self._unlocked:
            d6 = _characteristic_by_uuid(self._client, CHAR_D6_UUID)
            await self._unlock_channels(channel, d6)
        _LOGGER.info("Setting %s=%s on D5", key, value)
        raw = await self._write_and_wait(
            channel, set_command(key, value), timeout=UNLOCK_TIMEOUT
        )
        payload = _loads_json(raw)
        if rejected := _invalid_pin_from_payload(payload):
            raise rejected
        status = None if payload is None else payload.get("status")
        if status not in (0, None):
            raise BleakError(
                f"Appliance rejected set {key}={value} (status {status})"
            )

    async def async_verify_pin(self) -> None:
        """Pair and unlock with the configured PIN, then disconnect.

        Raises SubZeroInvalidPin or SubZeroPairingError if the appliance
        rejects the code. Disconnects afterwards so the single BLE slot is free.
        """
        if not self._pin:
            raise BleakError("PIN is required to verify pairing")
        async with self._lock:
            try:
                await self._connect_and_setup(require_pair=True)
                if not self._unlocked:
                    raise SubZeroInvalidPin(
                        "Appliance rejected PIN. Check the code on the display."
                    )
            finally:
                await self._disconnect_unlocked()

    async def async_disconnect(self) -> None:
        """Drop the BLE connection if it is still open."""
        async with self._lock:
            await self._disconnect_unlocked()

    async def _disconnect_unlocked(self) -> None:
        """Disconnect while already holding the client lock."""
        if self._pairing is not None:
            await self._pairing.stop()
            self._pairing = None
        self._closing = True
        client = self._client
        self._client = None
        self._poll_channel = None
        self._unlocked = False
        subscribed = list(self._subscribed)
        self._subscribed.clear()
        if client is None:
            return
        if client.is_connected:
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

    async def _ensure_connected(self, require_pair: bool = False) -> None:
        """Connect and subscribe if we are not already connected."""
        if self._client and self._client.is_connected and self._poll_channel:
            if require_pair and self._pin and not self._unlocked:
                # Existing D7 session is unencrypted; D5/D6 need a bonded link.
                await self._reconnect_encrypted(force_pair=False)
                return
            if self._pin and not self._unlocked:
                d5 = _characteristic_by_uuid(self._client, CHAR_D5_UUID)
                d6 = _characteristic_by_uuid(self._client, CHAR_D6_UUID)
                await self._unlock_channels(d5, d6)
                if d6 is not None and str(d6.uuid).lower() in self._subscribed:
                    self._poll_channel = d6
            return

        await self._connect_and_setup(require_pair=require_pair)

    async def _connect_and_setup(
        self,
        require_pair: bool = False,
        encrypt_reconnect: bool = True,
    ) -> None:
        """Establish GATT, optionally pair, then subscribe."""
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
        await asyncio.sleep(LINK_SETTLE_SECONDS)
        if self._pin:
            try:
                await self._ensure_paired()
            except SubZeroPairingError as err:
                if require_pair:
                    raise
                _LOGGER.warning("BLE pairing did not complete: %s", err)
            # Pair() on an existing bond does not encrypt this ATT session.
            # Drop and reconnect so BlueZ applies the stored LTK. D5/D6 CCCD
            # writes otherwise fail with GATT Insufficient authentication.
            if encrypt_reconnect:
                _LOGGER.info(
                    "Reconnecting so the GATT link uses the BLE bond"
                )
                await self._disconnect_unlocked()
                await asyncio.sleep(RECONNECT_GAP_SECONDS)
                await self._connect_and_setup(
                    require_pair=require_pair,
                    encrypt_reconnect=False,
                )
                return

        await self._subscribe_and_unlock()

    async def _ensure_paired(self) -> bool:
        """Pair if needed. Return True when a new SMP pairing completed."""
        assert self._client is not None
        assert self._pin is not None
        if await async_device_is_paired(self._ble_device, self._client):
            _LOGGER.info(
                "Already bonded with %s; skipping Pair()",
                self._ble_device.address,
            )
            return False
        if self._pairing is None:
            self._pairing = PairingAgentSession(self._pin)
            await self._pairing.start(bleak_message_bus(self._client))
        return await async_pair_with_passkey(
            self._ble_device,
            self._pin,
            client=self._client,
            session=self._pairing,
        )

    async def _reconnect_encrypted(self, force_pair: bool = False) -> None:
        """Drop the unencrypted ATT link and reconnect after pairing."""
        if force_pair and self._client is not None and hasattr(self._client, "unpair"):
            _LOGGER.info("Removing existing BlueZ bond for KeyboardOnly pairing")
            try:
                await self._client.unpair()
            except Exception as err:
                _LOGGER.debug("unpair: %s", err)
        await self._disconnect_unlocked()
        await asyncio.sleep(RECONNECT_GAP_SECONDS)
        await self._connect_and_setup(require_pair=True, encrypt_reconnect=False)

    async def _subscribe_and_unlock(self) -> None:
        """Subscribe to D7 (and D5/D6 after a PIN) and unlock encrypted channels."""
        assert self._client is not None
        d7 = _characteristic_by_uuid(self._client, CHAR_D7_UUID)
        d6 = _characteristic_by_uuid(self._client, CHAR_D6_UUID)
        d5 = _characteristic_by_uuid(self._client, CHAR_D5_UUID)
        if d7 is not None:
            await self._subscribe(d7)

        if self._pin:
            try:
                await self._subscribe_encrypted_channels(d5, d6)
                if not self._has_encrypted_subscription(d5, d6):
                    _LOGGER.info(
                        "D5/D6 need an encrypted ATT link; encrypting the existing bond"
                    )
                    await self._encrypt_existing_bond()
                    d5 = _characteristic_by_uuid(self._client, CHAR_D5_UUID)
                    d6 = _characteristic_by_uuid(self._client, CHAR_D6_UUID)
                    await self._subscribe_encrypted_channels(d5, d6)
            except SubZeroInvalidPin as err:
                _LOGGER.warning("%s", err)
                if d7 is None:
                    raise
                self._poll_channel = d7
            else:
                if d6 is not None and str(d6.uuid).lower() in self._subscribed:
                    self._poll_channel = d6
                    _LOGGER.info("Using D6 for full-state polling after unlock")
                elif d7 is not None:
                    self._poll_channel = d7
                    _LOGGER.warning(
                        "Unlock attempted; falling back to D7 polling. "
                        "D5/D6 still require encryption — the BlueZ bond may be stale."
                    )
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

    def _has_encrypted_subscription(
        self,
        d5: BleakGATTCharacteristic | None,
        d6: BleakGATTCharacteristic | None,
    ) -> bool:
        """Return True if D5 or D6 CCCD subscribe succeeded."""
        for channel in (d5, d6):
            if channel is not None and str(channel.uuid).lower() in self._subscribed:
                return True
        return False

    async def _subscribe_encrypted_channels(
        self,
        d5: BleakGATTCharacteristic | None,
        d6: BleakGATTCharacteristic | None,
    ) -> None:
        """Subscribe and unlock D5/D6 when they are visible."""
        if d5 is not None:
            await self._subscribe_optional(d5)
        if d6 is not None:
            await self._subscribe_optional(d6)
        await self._unlock_channels(d5, d6)

    async def _encrypt_existing_bond(self) -> None:
        """Ask BlueZ to encrypt this ATT connection with the stored LTK."""
        assert self._client is not None
        try:
            await asyncio.wait_for(self._client.pair(), timeout=PAIR_TIMEOUT)
        except Exception as err:
            message = str(err)
            if "AlreadyExists" in message or "already" in message.lower():
                return
            _LOGGER.debug("pair() to encrypt existing bond: %s", err)

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
            try:
                raw = await self._write_and_wait(
                    channel, command, timeout=UNLOCK_TIMEOUT, redact=True
                )
            except BleakError as err:
                if _is_insufficient_auth(err):
                    _LOGGER.warning(
                        "unlock_channel on %s needs an encrypted link: %s",
                        name,
                        err,
                    )
                    continue
                raise
            payload = _loads_json(raw)
            if rejected := _invalid_pin_from_payload(payload):
                raise rejected
            status = None if payload is None else payload.get("status")
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
            fields=dict(fields),
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


def _is_insufficient_auth(err: BaseException) -> bool:
    """Return True if BlueZ rejected a GATT op because the link is not bonded."""
    text = str(err).lower()
    return any(
        marker in text
        for marker in (
            "insufficient authentication",
            "insufficient encryption",
            "error=5",
            "error=8",
            "authentication is required",
            "not paired",
        )
    )


def _is_display_pin_retryable(err: BaseException) -> bool:
    """Return True if display_pin should be sent again (door likely closed)."""
    text = str(err).lower()
    if "enter the 6-digit pin" in text:
        return False
    return True


def _is_link_drop(err: BaseException) -> bool:
    """Return True if the appliance or adapter dropped the GATT connection."""
    text = str(err).lower()
    return any(
        marker in text
        for marker in (
            "disconnected during poll",
            "disconnected",
            "not connected",
            "connection lost",
            "unlikely error",
            "gatt protocol error",
        )
    )


def _invalid_pin_from_payload(
    payload: dict[str, Any] | None,
) -> SubZeroInvalidPin | None:
    """Return an error if unlock/set JSON says the PIN was rejected."""
    if payload is None:
        return None
    status = payload.get("status")
    lockout: int | None = None
    resp = payload.get("resp")
    if isinstance(resp, dict) and resp.get("lockout_duration") is not None:
        try:
            lockout = int(resp["lockout_duration"])
        except (TypeError, ValueError):
            lockout = None
    if status not in (3, 302) and lockout is None:
        return None
    status_int = status if isinstance(status, int) else None
    if lockout:
        return SubZeroInvalidPin(
            f"Appliance rejected PIN (status {status}). "
            f"Wait {lockout} seconds before trying again.",
            status=status_int,
            lockout_seconds=lockout,
        )
    return SubZeroInvalidPin(
        f"Appliance rejected PIN (status {status}). "
        "Check the code on the display.",
        status=status_int,
    )


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
    """Return the first newline-delimited JSON object from buffer, if present.

    Appliance frames are compact JSON plus a trailing linefeed (0x0a). BLE
    may split a frame across indications; wait for that byte before parsing.
    """
    while True:
        newline = buffer.find(b"\n")
        if newline == -1:
            return None
        frame = bytes(buffer[:newline])
        del buffer[: newline + 1]
        start = frame.find(b"{")
        if start == -1:
            continue
        return frame[start:]


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
