"""Test the Sub-Zero BLE config, options, and reauth flows."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bleak.exc import BleakError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.subzero_ble.client import SubZeroInvalidPin
from custom_components.subzero_ble.config_flow import (
    SubZeroBLEConfigFlow,
    _async_cancel_task,
    _pin_verify_error,
    _title,
)
from custom_components.subzero_ble.const import CONF_PIN, DOMAIN
from custom_components.subzero_ble.pairing import SubZeroPairingError
from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_REAUTH, SOURCE_USER
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import ADDRESS, DEVICE_NAME, PIN, add_entry

OTHER_ADDRESS = "11:22:33:44:55:66"


def _info(name: str = DEVICE_NAME, address: str = ADDRESS) -> SimpleNamespace:
    """Return advertisement fields used by the config flow."""
    return SimpleNamespace(name=name, address=address)


async def _start_user(hass: HomeAssistant, devices: list[SimpleNamespace] | None = None):
    """Open the user flow with the given advertisements."""
    if devices is None:
        devices = [_info()]
    with patch(
        "custom_components.subzero_ble.config_flow.async_discovered_service_info",
        return_value=devices,
    ):
        return await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )


async def _start_bluetooth(
    hass: HomeAssistant, discovery_info: SimpleNamespace | None = None
):
    """Open a Bluetooth discovery flow."""
    return await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=discovery_info or _info(),
    )


async def test_user_full_flow_with_pin(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_ble_client: MagicMock,
    mock_ble_device: MagicMock,
) -> None:
    """Test selecting a discovered appliance and pairing with a PIN."""
    result = await _start_user(
        hass, [_info(name="Other", address=OTHER_ADDRESS), _info()]
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pin"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: PIN}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == DEVICE_NAME
    assert result["data"] == {CONF_ADDRESS: ADDRESS, CONF_PIN: PIN}
    assert result["result"].unique_id == ADDRESS
    mock_setup_entry.assert_called_once()
    mock_ble_client.async_verify_pin.assert_awaited_once()


async def test_user_diagnostic_only_skips_verify(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_ble_client: MagicMock,
    mock_ble_device: MagicMock,
) -> None:
    """Test leaving the PIN blank creates a diagnostic-only entry."""
    result = await _start_user(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: ""}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: ADDRESS}
    mock_ble_client.async_verify_pin.assert_not_called()


async def test_user_no_devices_found(hass: HomeAssistant) -> None:
    """Test the user step aborts when nothing Sub-Zero is advertising."""
    result = await _start_user(hass, [_info(name="NotAFridge", address=OTHER_ADDRESS)])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_user_skips_already_configured(hass: HomeAssistant) -> None:
    """Test advertisements that already have an entry are omitted."""
    await add_entry(hass)
    result = await _start_user(hass, [_info()])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_user_already_configured_on_submit(hass: HomeAssistant) -> None:
    """Test picking an address that was configured after the form opened."""
    result = await _start_user(hass)
    await add_entry(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_user_includes_bluetooth_discovery(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_ble_client: MagicMock,
    mock_ble_device: MagicMock,
) -> None:
    """Test the user step lists a discovery already stored on the flow."""
    flow = SubZeroBLEConfigFlow()
    flow.hass = hass
    flow.context = {}
    flow._discovery_info = _info()
    with patch(
        "custom_components.subzero_ble.config_flow.async_discovered_service_info",
        return_value=[],
    ):
        result = await flow.async_step_user()
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await flow.async_step_user({CONF_ADDRESS: ADDRESS})
    result = await flow.async_step_pin({CONF_PIN: PIN})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: ADDRESS, CONF_PIN: PIN}


async def test_bluetooth_full_flow(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_ble_client: MagicMock,
    mock_ble_device: MagicMock,
) -> None:
    """Test Bluetooth discovery, confirm, and PIN create an entry."""
    result = await _start_bluetooth(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pin"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: PIN}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == DEVICE_NAME
    assert result["data"] == {CONF_ADDRESS: ADDRESS, CONF_PIN: PIN}


async def test_bluetooth_not_supported(hass: HomeAssistant) -> None:
    """Test a non-SZG advertisement is rejected."""
    result = await _start_bluetooth(hass, _info(name="Kitchen Speaker"))
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"


async def test_bluetooth_already_configured(hass: HomeAssistant) -> None:
    """Test discovery aborts when the appliance is already set up."""
    await add_entry(hass)
    result = await _start_bluetooth(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_bluetooth_already_in_progress(hass: HomeAssistant) -> None:
    """Test a second discovery for the same address is aborted."""
    result = await _start_bluetooth(hass)
    assert result["type"] is FlowResultType.FORM
    result = await _start_bluetooth(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_in_progress"


async def test_bluetooth_title_when_name_matches_address(
    hass: HomeAssistant,
) -> None:
    """Test confirm copy when the local name is the same as the address."""
    discovery = _info(name="SZGTEST", address="SZGTEST")
    result = await _start_bluetooth(hass, discovery)
    assert result["type"] is FlowResultType.FORM
    assert result["description_placeholders"]["name"] == "Sub-Zero (SZGTEST)"


async def test_pin_invalid_then_success(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_ble_client: MagicMock,
    mock_ble_device: MagicMock,
) -> None:
    """Test a malformed PIN can be corrected and the flow finishes."""
    result = await _start_user(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: "12"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_PIN: "invalid_pin"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: PIN}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PIN] == PIN


async def test_pin_invalid_auth_then_success(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_ble_client: MagicMock,
    mock_ble_device: MagicMock,
) -> None:
    """Test an appliance PIN rejection can be retried."""
    mock_ble_client.async_verify_pin.side_effect = [
        SubZeroInvalidPin("rejected"),
        None,
    ]
    result = await _start_user(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: PIN}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: PIN}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_pin_pairing_error_maps_to_cannot_connect(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_ble_client: MagicMock,
    mock_ble_device: MagicMock,
) -> None:
    """Test a pairing infrastructure failure is shown as cannot_connect, then succeeds."""
    mock_ble_client.async_verify_pin.side_effect = [
        SubZeroPairingError("pair failed"),
        None,
    ]
    result = await _start_user(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: PIN}
    )
    assert result["errors"] == {"base": "cannot_connect"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: PIN}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_pin_cannot_connect_then_success(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_ble_client: MagicMock,
) -> None:
    """Test a missing appliance can be retried once it is in range."""
    with patch(
        "custom_components.subzero_ble.config_flow.bluetooth.async_ble_device_from_address",
        side_effect=[None, None, None, MagicMock()],
    ):
        result = await _start_user(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESS: ADDRESS}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PIN: PIN}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PIN: PIN}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_pin_timeout_maps_to_cannot_connect(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_ble_client: MagicMock,
    mock_ble_device: MagicMock,
) -> None:
    """Test a BLE timeout is shown as cannot_connect, then succeeds."""
    mock_ble_client.async_verify_pin.side_effect = [TimeoutError("timeout"), None]
    result = await _start_user(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: PIN}
    )
    assert result["errors"] == {"base": "cannot_connect"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: PIN}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_pin_oserror_maps_to_cannot_connect(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_ble_client: MagicMock,
    mock_ble_device: MagicMock,
) -> None:
    """Test an OSError during verify is shown as cannot_connect, then succeeds."""
    mock_ble_client.async_verify_pin.side_effect = [OSError("lost"), None]
    result = await _start_user(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: PIN}
    )
    assert result["errors"] == {"base": "cannot_connect"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: PIN}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_pin_display_pin_not_in_range(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_ble_client: MagicMock,
) -> None:
    """Test the PIN form still opens when Show PIN cannot find the appliance."""
    with patch(
        "custom_components.subzero_ble.config_flow.bluetooth.async_ble_device_from_address",
        return_value=None,
    ):
        result = await _start_user(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESS: ADDRESS}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pin"
    mock_ble_client.async_display_pin.assert_not_called()


async def test_pin_display_pin_error_is_logged(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_ble_client: MagicMock,
    mock_ble_device: MagicMock,
) -> None:
    """Test a Show PIN failure is swallowed so the form still appears."""
    mock_ble_client.async_display_pin.side_effect = OSError("dropped")
    result = await _start_user(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.FORM
    mock_ble_client.async_disconnect.assert_awaited()


async def test_pin_display_pin_cancelled_on_submit(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_ble_client: MagicMock,
    mock_ble_device: MagicMock,
) -> None:
    """Test submitting a PIN cancels an in-flight Show PIN loop."""

    async def _hang() -> None:
        await asyncio.Event().wait()

    mock_ble_client.async_display_pin.side_effect = _hang
    result = await _start_user(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS}
    )
    await asyncio.sleep(0)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: PIN}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_pin_invalid_keeps_show_pin_running(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_ble_client: MagicMock,
    mock_ble_device: MagicMock,
) -> None:
    """Test a malformed PIN does not restart an in-flight Show PIN loop."""

    async def _hang() -> None:
        await asyncio.Event().wait()

    mock_ble_client.async_display_pin.side_effect = _hang
    result = await _start_user(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS}
    )
    await asyncio.sleep(0)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: "12"}
    )
    assert result["errors"] == {CONF_PIN: "invalid_pin"}
    assert mock_ble_client.async_display_pin.call_count == 1
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: PIN}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_pin_form_without_name(
    hass: HomeAssistant, mock_ble_device: MagicMock, mock_ble_client: MagicMock
) -> None:
    """Test PIN placeholders fall back when the flow has no title yet."""
    flow = SubZeroBLEConfigFlow()
    flow.hass = hass
    flow.context = {}
    flow._address = ADDRESS
    result = await flow.async_step_pin()
    assert result["description_placeholders"]["name"] == "the appliance"
    result = await flow.async_step_pin({CONF_PIN: ""})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == ADDRESS


async def test_ensure_display_pin_without_address(hass: HomeAssistant) -> None:
    """Test Show PIN is skipped until an address is known."""
    flow = SubZeroBLEConfigFlow()
    flow.hass = hass
    await flow._async_ensure_display_pin()
    assert flow._display_pin_task is None


async def test_reauth_success(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_ble_client: MagicMock,
    mock_ble_device: MagicMock,
) -> None:
    """Test reauth stores a new PIN and aborts successfully."""
    entry = await add_entry(hass, **{CONF_PIN: "000000"})
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=dict(entry.data),
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: PIN}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PIN] == PIN


async def test_reauth_invalid_then_success(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_ble_client: MagicMock,
    mock_ble_device: MagicMock,
) -> None:
    """Test reauth recovers from a malformed PIN and a rejected PIN."""
    entry = await add_entry(hass, **{CONF_PIN: "000000"})
    mock_ble_client.async_verify_pin.side_effect = [
        SubZeroInvalidPin("rejected"),
        None,
    ]
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=dict(entry.data),
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: "abc"}
    )
    assert result["errors"] == {CONF_PIN: "invalid_pin"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: ""}
    )
    assert result["errors"] == {CONF_PIN: "invalid_pin"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: PIN}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: PIN}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"


async def test_reauth_cannot_connect_restores_poll_interval(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_ble_client: MagicMock,
    mock_ble_device: MagicMock,
) -> None:
    """Test a failed reauth verify puts the coordinator poll interval back."""
    entry = await add_entry(hass, **{CONF_PIN: "000000"})
    saved = timedelta(seconds=10)
    coordinator = SimpleNamespace(
        update_interval=saved,
        async_disconnect=AsyncMock(),
        async_ensure_display_pin=AsyncMock(),
    )
    entry.runtime_data = coordinator
    mock_ble_client.async_verify_pin.side_effect = BleakError("busy")

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=dict(entry.data),
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: PIN}
    )
    assert result["errors"] == {"base": "cannot_connect"}
    assert coordinator.update_interval == saved
    coordinator.async_disconnect.assert_awaited_once()

    mock_ble_client.async_verify_pin.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: PIN}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"


async def test_options_add_pin_pairing_error(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_ble_client: MagicMock,
    mock_ble_device: MagicMock,
) -> None:
    """Test Configure after diagnostic-only setup does not treat a pairing miss as a bad PIN."""
    entry = await add_entry(hass)
    mock_ble_client.async_verify_pin.side_effect = SubZeroPairingError(
        "Cannot pair: BlueZ device path is unavailable on this adapter"
    )
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_PIN: PIN}
    )
    assert result["errors"] == {"base": "cannot_connect"}
    assert CONF_PIN not in entry.data


async def test_options_update_pin(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_ble_client: MagicMock,
    mock_ble_device: MagicMock,
) -> None:
    """Test Configure saves a new PIN."""
    entry = await add_entry(hass, **{CONF_PIN: "000000"})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_PIN: PIN}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_PIN] == PIN


async def test_options_clear_pin(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_ble_client: MagicMock,
    mock_ble_device: MagicMock,
) -> None:
    """Test Configure can drop the PIN for diagnostic-only mode."""
    entry = await add_entry(hass, **{CONF_PIN: PIN})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_PIN: ""}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_PIN not in entry.data
    mock_ble_client.async_verify_pin.assert_not_called()


async def test_options_invalid_then_success(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_ble_client: MagicMock,
    mock_ble_device: MagicMock,
) -> None:
    """Test Configure recovers from a malformed PIN and a BLE error."""
    entry = await add_entry(hass, **{CONF_PIN: PIN})
    mock_ble_client.async_verify_pin.side_effect = [BleakError("busy"), None]
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_PIN: "nope"}
    )
    assert result["errors"] == {CONF_PIN: "invalid_pin"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_PIN: "654321"}
    )
    assert result["errors"] == {"base": "cannot_connect"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_PIN: "654321"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_PIN] == "654321"


async def test_options_invalid_keeps_show_pin_running(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_ble_client: MagicMock,
    mock_ble_device: MagicMock,
) -> None:
    """Test Configure does not restart Show PIN after a malformed PIN."""

    async def _hang() -> None:
        await asyncio.Event().wait()

    entry = await add_entry(hass, **{CONF_PIN: PIN})
    mock_ble_client.async_display_pin.side_effect = _hang
    result = await hass.config_entries.options.async_init(entry.entry_id)
    await asyncio.sleep(0)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_PIN: "nope"}
    )
    assert result["errors"] == {CONF_PIN: "invalid_pin"}
    assert mock_ble_client.async_display_pin.call_count == 1
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_PIN: "654321"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_PIN] == "654321"


async def test_options_verify_failure_restores_interval(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_ble_client: MagicMock,
    mock_ble_device: MagicMock,
) -> None:
    """Test Configure puts the coordinator poll interval back after failure."""
    entry = await add_entry(hass, **{CONF_PIN: PIN})
    saved = timedelta(seconds=10)
    coordinator = SimpleNamespace(
        update_interval=saved,
        async_disconnect=AsyncMock(),
        async_ensure_display_pin=AsyncMock(),
    )
    entry.runtime_data = coordinator
    mock_ble_client.async_verify_pin.side_effect = SubZeroInvalidPin("rejected")

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_PIN: "654321"}
    )
    assert result["errors"] == {"base": "invalid_auth"}
    assert coordinator.update_interval == saved


async def test_options_uses_coordinator_display_pin(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_ble_client: MagicMock,
    mock_ble_device: MagicMock,
) -> None:
    """Test Configure asks the running coordinator to show the PIN."""
    entry = await add_entry(hass, **{CONF_PIN: PIN})
    coordinator = SimpleNamespace(async_ensure_display_pin=AsyncMock())
    entry.runtime_data = coordinator
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    coordinator.async_ensure_display_pin.assert_awaited_once()
    mock_ble_client.async_display_pin.assert_not_called()


async def test_options_coordinator_display_pin_error(
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
    mock_ble_client: MagicMock,
    mock_ble_device: MagicMock,
) -> None:
    """Test Configure still opens when the coordinator Show PIN fails."""
    entry = await add_entry(hass, **{CONF_PIN: PIN})
    coordinator = SimpleNamespace(
        async_ensure_display_pin=AsyncMock(side_effect=OSError("busy"))
    )
    entry.runtime_data = coordinator
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM


async def test_options_no_address_skips_standalone_display_pin(
    hass: HomeAssistant, mock_setup_entry: AsyncMock
) -> None:
    """Test Configure without an address does not start a BLE Show PIN loop."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id="missing", data={}, title="x")
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM


async def test_title_helpers() -> None:
    """Test advertisement titles used on discovery forms."""
    assert _title(_info()) == DEVICE_NAME
    assert _title(_info(name="")) == f"Sub-Zero ({ADDRESS})"
    assert _pin_verify_error(SubZeroInvalidPin("bad")) == "invalid_auth"
    assert _pin_verify_error(SubZeroPairingError("pair")) == "cannot_connect"
    assert _pin_verify_error(BleakError("lost")) == "cannot_connect"


async def test_cancel_task_noop() -> None:
    """Test cancelling a missing or finished task is a no-op."""
    await _async_cancel_task(None)

    async def _done() -> None:
        return None

    task = asyncio.get_running_loop().create_task(_done())
    await task
    await _async_cancel_task(task)
