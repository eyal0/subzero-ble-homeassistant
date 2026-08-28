"""Constants for the Sub-Zero BLE integration."""

from __future__ import annotations

import json
from pathlib import Path
import re

DOMAIN = "subzero_ble"
VERSION = json.loads(
    (Path(__file__).resolve().parent / "manifest.json").read_text(encoding="utf-8")
)["version"]

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
# BlueZ needs a beat after connect before CCCD writes, especially on a
# reconnect that immediately follows Pair() on this one-connection appliance.
LINK_SETTLE_SECONDS = 0.4
# The appliance allows one BLE connection; wait for the slot after we drop it
# so the follow-up connect can encrypt with the stored bond.
RECONNECT_GAP_SECONDS = 1.0
PAIR_TIMEOUT = 60.0
UNLOCK_TIMEOUT = 15.0
DISPLAY_PIN_DURATION = 20
DISPLAY_PIN_RETRY_SECONDS = 3.0
# Door-closed display_pin fails; keep trying long enough to walk over and open it.
DISPLAY_PIN_RETRY_TIMEOUT = 120.0
MAX_FRAME_BYTES = 4096
UPDATE_INTERVAL_SECONDS = 10
# ESPHome found back-to-back D5 sets can drop; space grouped flag writes.
SET_WRITE_GAP_SECONDS = 0.75

CONNECTION_NOT_IN_RANGE = "Not in range"
CONNECTION_DISCONNECTED = "Disconnected"
CONNECTION_DIAGNOSTIC = "Diagnostic only"
CONNECTION_CONNECTED = "Connected"
CONNECTION_PAIRED = "Paired"
CONNECTION_INVALID_PIN = "Invalid PIN"
CONNECTION_STATUSES = (
    CONNECTION_NOT_IN_RANGE,
    CONNECTION_DISCONNECTED,
    CONNECTION_DIAGNOSTIC,
    CONNECTION_CONNECTED,
    CONNECTION_PAIRED,
    CONNECTION_INVALID_PIN,
)

ICE_MAKER_OFF = "Off"
ICE_MAKER_NORMAL = "Normal"
ICE_MAKER_NIGHT_ICE = "Night Ice"
ICE_MAKER_MAX_ICE = "Max Ice"
ICE_MAKER_OPTIONS = (
    ICE_MAKER_NORMAL,
    ICE_MAKER_MAX_ICE,
    ICE_MAKER_NIGHT_ICE,
    ICE_MAKER_OFF,
)

# BLE has no ice-mode verb; the four UI states are these three booleans.
ICE_MAKER_MODE_PARAMS: dict[str, dict[str, bool]] = {
    ICE_MAKER_OFF: {
        "ice_maker_on": False,
        "max_ice_on": False,
        "night_ice_on": False,
    },
    ICE_MAKER_NORMAL: {
        "ice_maker_on": True,
        "max_ice_on": False,
        "night_ice_on": False,
    },
    ICE_MAKER_NIGHT_ICE: {
        "ice_maker_on": True,
        "max_ice_on": False,
        "night_ice_on": True,
    },
    ICE_MAKER_MAX_ICE: {
        "ice_maker_on": True,
        "max_ice_on": True,
        "night_ice_on": False,
    },
}

APPLIANCE_NORMAL = "Normal"
APPLIANCE_HIGH_USAGE = "High Usage"
APPLIANCE_SHORT_VACATION = "Short Vacation"
APPLIANCE_LONG_VACATION = "Long Vacation"
APPLIANCE_SABBATH = "Sabbath"
APPLIANCE_MODE_OPTIONS = (
    APPLIANCE_NORMAL,
    APPLIANCE_HIGH_USAGE,
    APPLIANCE_SHORT_VACATION,
    APPLIANCE_LONG_VACATION,
    APPLIANCE_SABBATH,
)

APPLIANCE_MODE_FLAGS = (
    "high_use_on",
    "short_vacation_on",
    "long_vacation_on",
    "sabbath_on",
)


def _appliance_mode_params(active: str | None) -> dict[str, bool]:
    return {key: key == active for key in APPLIANCE_MODE_FLAGS}


# BLE has no appliance-mode verb; the five UI states are these four booleans.
APPLIANCE_MODE_PARAMS: dict[str, dict[str, bool]] = {
    APPLIANCE_NORMAL: _appliance_mode_params(None),
    APPLIANCE_HIGH_USAGE: _appliance_mode_params("high_use_on"),
    APPLIANCE_SHORT_VACATION: _appliance_mode_params("short_vacation_on"),
    APPLIANCE_LONG_VACATION: _appliance_mode_params("long_vacation_on"),
    APPLIANCE_SABBATH: _appliance_mode_params("sabbath_on"),
}

HUMIDITY_NORMAL = "Normal"
HUMIDITY_ENHANCED = "Enhanced"
HUMIDITY_OPTIONS = (HUMIDITY_NORMAL, HUMIDITY_ENHANCED)
# Confirmed on-appliance: 1=Normal, 2=Enhanced. 0 is ignored.
HUMIDITY_CONTROL_VALUES: dict[str, int] = {
    HUMIDITY_NORMAL: 1,
    HUMIDITY_ENHANCED: 2,
}
HUMIDITY_CONTROL_LABELS: dict[int, str] = {
    value: label for label, value in HUMIDITY_CONTROL_VALUES.items()
}


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


def display_pin_command(duration: int = DISPLAY_PIN_DURATION) -> bytes:
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
