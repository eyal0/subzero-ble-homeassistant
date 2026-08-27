"""Constants for the Sub-Zero BLE integration."""

DOMAIN = "subzero_ble"

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

POLL_TIMEOUT = 15.0
MAX_FRAME_BYTES = 4096
