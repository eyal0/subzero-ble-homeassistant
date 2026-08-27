"""Constants for the Sub-Zero BLE integration."""

from __future__ import annotations

import json
import re

DOMAIN = "subzero_ble"
VERSION = "0.9.0"

CONF_PIN = "pin"

# Advertised local names start with SZG (Sub-Zero Group).
LOCAL_NAME_PREFIX = "SZG"

# Custom GATT service used by Sub-Zero Group appliances.
SERVICE_UUID = "e20a39f4-73f5-4bc4-a12f-17d1ad07a961"

# Characteristic UUIDs (suffix D4–D8). D5/D6 are encrypted and only appear
# after BLE bonding. D7 is the open pre-auth diagnostic channel.
CHAR_D4_UUID = "08590f7e-db05-467e-8757-72f6faeb13d4"
CHAR_D5_UUID = "08590f7e-db05-467e-8757-72f6faeb13d5"
CHAR_D6_UUID = "08590f7e-db05-467e-8757-72f6faeb13d6"
CHAR_D7_UUID = "08590f7e-db05-467e-8757-72f6faeb13d7"
CHAR_D8_UUID = "08590f7e-db05-467e-8757-72f6faeb13d8"

GET_ASYNC_COMMAND = b'{"cmd":"get_async"}\n'
GET_COMMAND = b'{"cmd":"get"}\n'

PIN_PATTERN = re.compile(r"^\d{6}$")

POLL_TIMEOUT = 15.0
SUBSCRIBE_TIMEOUT = 5.0
CONNECT_TIMEOUT = 30.0
PAIR_TIMEOUT = 60.0
UNLOCK_TIMEOUT = 15.0
MAX_FRAME_BYTES = 4096
UPDATE_INTERVAL_SECONDS = 10


def normalize_pin(pin: str | None) -> str | None:
    """Return a 6-digit PIN or None if the value is empty."""
    if pin is None:
        return None
    stripped = pin.strip()
    if not stripped:
        return None
    if not PIN_PATTERN.fullmatch(stripped):
        raise ValueError("PIN must be exactly 6 digits")
    return stripped


def unlock_command(pin: str) -> bytes:
    """Build an unlock_channel command. Do not log the returned payload."""
    return (
        json.dumps(
            {"cmd": "unlock_channel", "params": {"pin": pin}},
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def display_pin_command(duration: int = 30) -> bytes:
    """Build a display_pin command that shows the PIN on the appliance."""
    return (
        json.dumps(
            {"cmd": "display_pin", "params": {"duration": duration}},
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def set_command(key: str, value: object) -> bytes:
    """Build a D5 set command for a single appliance property."""
    return (
        json.dumps(
            {"cmd": "set", "params": {key: value}},
            separators=(",", ":"),
        )
        + "\n"
    ).encode()
