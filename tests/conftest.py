"""Shared fixtures for Sub-Zero BLE tests."""

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant

pytest_plugins = "pytest_homeassistant_custom_component"

ADDRESS = "AA:BB:CC:DD:EE:FF"
PIN = "123456"
DEVICE_NAME = "SZG DEU2450C"
DOMAIN = "subzero_ble"
CONF_PIN = "pin"



@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading this repo's custom_components during tests."""


@pytest.fixture(autouse=True)
def skip_persistent_notifications() -> Generator[None]:
    """Skip persistent notification helpers that are not loaded in tests."""
    with (
        patch("homeassistant.components.persistent_notification.async_create"),
        patch("homeassistant.components.persistent_notification.async_dismiss"),
    ):
        yield


@pytest.fixture(autouse=True)
def mock_active_scan() -> Generator[None]:
    """Avoid calling into the bluetooth integration during the user step."""
    with patch(
        "custom_components.subzero_ble.config_flow.bluetooth.async_request_active_scan",
        new=AsyncMock(),
        create=True,
    ):
        yield


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Prevent the integration from connecting over BLE after a flow succeeds."""
    with patch(
        "custom_components.subzero_ble.async_setup_entry",
        return_value=True,
    ) as mock:
        yield mock


@pytest.fixture
def mock_ble_client() -> Generator[MagicMock]:
    """Replace the BLE client used to show and verify the PIN."""
    with patch(
        "custom_components.subzero_ble.config_flow.SubZeroBleClient"
    ) as mock_cls:
        client = mock_cls.return_value
        client.async_verify_pin = AsyncMock()
        client.async_display_pin = AsyncMock()
        client.async_disconnect = AsyncMock()
        yield client


@pytest.fixture
def mock_ble_device() -> Generator[MagicMock]:
    """Return a BLE device so PIN verify and Show PIN can run."""
    with patch(
        "custom_components.subzero_ble.config_flow.bluetooth.async_ble_device_from_address",
        return_value=MagicMock(),
    ) as mock:
        yield mock


def mock_entry(**data: object) -> MockConfigEntry:
    """Return a config entry for the test appliance."""
    entry_data = {CONF_ADDRESS: ADDRESS, **data}
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        title=DEVICE_NAME,
        data=entry_data,
    )


async def add_entry(hass: HomeAssistant, **data: object) -> MockConfigEntry:
    """Add a Sub-Zero config entry to hass without setting up platforms."""
    entry = mock_entry(**data)
    entry.add_to_hass(hass)
    return entry
