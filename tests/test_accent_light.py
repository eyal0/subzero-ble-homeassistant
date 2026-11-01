"""Accent light level tracking and conditional switch creation."""

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.subzero_ble.const import (
    ACCENT_LIGHT_DEFAULT_ON_LEVEL,
    ACCENT_LIGHT_LEVEL,
    DOMAIN,
)
from custom_components.subzero_ble.coordinator import (
    SubZeroData,
    SubZeroDataUpdateCoordinator,
    accent_light_is_on,
    accent_light_on_level,
)
from custom_components.subzero_ble.switch import (
    ACCENT_LIGHT_DESCRIPTION,
    SWITCH_DESCRIPTIONS,
)

from .conftest import ADDRESS, mock_entry


def _with_level(level: object) -> SubZeroData:
    return SubZeroData().merge(SubZeroData(fields={ACCENT_LIGHT_LEVEL: level}))


def test_missing_level_stays_unknown_and_turn_on_uses_default() -> None:
    """A model that never sends the field stays unknown and writes 30."""
    data = SubZeroData()

    assert accent_light_is_on(data) is None
    assert data.accent_on_level is None
    assert accent_light_on_level(data) == ACCENT_LIGHT_DEFAULT_ON_LEVEL
    assert accent_light_on_level(None) == ACCENT_LIGHT_DEFAULT_ON_LEVEL


def test_positive_levels_are_on_and_remembered() -> None:
    """Each model's on-level is remembered for the next turn-on write."""
    for level in (30, 50, 130, 50.0):
        data = _with_level(level)

        assert accent_light_is_on(data) is True
        assert data.accent_on_level == int(level)
        assert accent_light_on_level(data) == int(level)


def test_off_keeps_the_last_on_level() -> None:
    """Turning the light off must not forget the level to restore."""
    on = _with_level(130)
    off = on.merge(SubZeroData(fields={ACCENT_LIGHT_LEVEL: 0}))

    assert accent_light_is_on(off) is False
    assert off.fields[ACCENT_LIGHT_LEVEL] == 0
    assert off.accent_on_level == 130
    assert accent_light_on_level(off) == 130


def test_partial_push_keeps_the_remembered_level() -> None:
    """A door push that omits the light leaves the last on-level in place."""
    on = _with_level(50)
    merged = on.merge(SubZeroData(fields={"ref_door_ajar": True}))

    assert merged.accent_on_level == 50
    assert merged.fields[ACCENT_LIGHT_LEVEL] == 50
    assert accent_light_on_level(merged) == 50


def test_new_on_level_replaces_the_remembered_one() -> None:
    """A later panel change to a different brightness becomes the restore value."""
    first = _with_level(30)
    updated = first.merge(SubZeroData(fields={ACCENT_LIGHT_LEVEL: 130}))

    assert updated.accent_on_level == 130
    assert accent_light_on_level(updated) == 130


def test_non_levels_do_not_replace_a_remembered_level() -> None:
    """Booleans, negatives, and text are not on-levels."""
    remembered = _with_level(30)

    for value in (True, False, -1, "high", None):
        updated = remembered.merge(SubZeroData(fields={ACCENT_LIGHT_LEVEL: value}))
        assert updated.accent_on_level == 30
        if value == -1:
            assert accent_light_is_on(updated) is False
        else:
            assert accent_light_is_on(updated) is None


def test_accent_light_switch_writes_the_remembered_level() -> None:
    """The switch turns on at the last reported level and off at 0."""
    description = ACCENT_LIGHT_DESCRIPTION
    assert [item.key for item in SWITCH_DESCRIPTIONS] == ["air_purifier", "night_mode"]
    off = _with_level(130).merge(SubZeroData(fields={ACCENT_LIGHT_LEVEL: 0}))

    assert description.property_key == ACCENT_LIGHT_LEVEL
    assert description.off_value == 0
    assert description.is_on_fn(off) is False
    assert description.on_value_fn is not None
    assert description.on_value_fn(off) == 130
    assert description.on_value_fn(SubZeroData()) == ACCENT_LIGHT_DEFAULT_ON_LEVEL


def _accent_entity_id(hass: HomeAssistant) -> str | None:
    """Return the accent light entity id, if it is registered."""
    return er.async_get(hass).async_get_entity_id(
        "switch", DOMAIN, f"{ADDRESS}_accent_light"
    )


@pytest.fixture
def ble_client() -> Generator[MagicMock]:
    """Poll through a stand-in BLE client so switch setup can run."""
    client = MagicMock()
    client.poll_state = AsyncMock(
        return_value=SubZeroData(fields={"appliance_model": "DEU2450C"})
    )
    client.connection_status.return_value = "paired"
    client.async_disconnect = AsyncMock()
    client.update_pin.return_value = False
    with (
        patch(
            "custom_components.subzero_ble.coordinator.bluetooth.async_ble_device_from_address",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.subzero_ble.coordinator.SubZeroBleClient",
            return_value=client,
        ),
    ):
        yield client


async def test_poll_and_push_decide_whether_accent_light_was_reported(
    hass: HomeAssistant,
) -> None:
    """A full poll can clear the report. A push and an empty poll cannot."""
    entry = mock_entry()
    entry.add_to_hass(hass)
    coordinator = SubZeroDataUpdateCoordinator(hass, entry)
    client = MagicMock()
    client.update_pin.return_value = False
    client.connection_status.return_value = "paired"
    client.poll_state = AsyncMock(
        return_value=SubZeroData(fields={ACCENT_LIGHT_LEVEL: 0})
    )
    coordinator.client = client

    with patch(
        "custom_components.subzero_ble.coordinator.bluetooth.async_ble_device_from_address",
        return_value=MagicMock(),
    ):
        await coordinator._async_update_data()
        assert coordinator.accent_light_reported is True

        coordinator._apply_push(SubZeroData(fields={"ref_door_ajar": True}))
        assert coordinator.accent_light_reported is True

        client.poll_state.return_value = SubZeroData(
            fields={"appliance_model": "DEU2450C"}
        )
        await coordinator._async_update_data()
        assert coordinator.accent_light_reported is False

        coordinator._apply_push(SubZeroData(fields={ACCENT_LIGHT_LEVEL: 30}))
        assert coordinator.accent_light_reported is True

        client.poll_state.return_value = SubZeroData()
        await coordinator._async_update_data()
        assert coordinator.accent_light_reported is True

    fresh = SubZeroDataUpdateCoordinator(hass, entry)
    fresh._apply_push(SubZeroData(fields={"ref_door_ajar": True}))
    assert fresh.accent_light_reported is None


async def test_stale_accent_light_is_removed_when_the_poll_omits_it(
    hass: HomeAssistant, ble_client: MagicMock
) -> None:
    """An Accent Light left from an older install goes away on a poll without it."""
    entry = mock_entry()
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "switch",
        DOMAIN,
        f"{ADDRESS}_accent_light",
        config_entry=entry,
        original_name="Accent Light",
    )
    ble_client.poll_state.return_value = SubZeroData(
        fields={"appliance_model": "DEU2450C", "air_filter_on": True}
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.runtime_data.accent_light_reported is False
    assert _accent_entity_id(hass) is None
    assert registry.async_get_entity_id("switch", DOMAIN, f"{ADDRESS}_air_purifier")
    assert registry.async_get_entity_id("switch", DOMAIN, f"{ADDRESS}_night_mode")
    await hass.config_entries.async_unload(entry.entry_id)


async def test_accent_light_switch_follows_later_polls(
    hass: HomeAssistant, ble_client: MagicMock
) -> None:
    """The switch appears, stays through partial updates, and can come back."""
    entry = mock_entry()
    entry.add_to_hass(hass)
    ble_client.poll_state.return_value = SubZeroData(
        fields={ACCENT_LIGHT_LEVEL: 30, "appliance_model": "IW30R"}
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    entity_id = _accent_entity_id(hass)
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "on"

    coordinator._apply_push(SubZeroData(fields={"ref_door_ajar": True}))
    await hass.async_block_till_done()
    assert _accent_entity_id(hass) == entity_id
    assert coordinator.accent_light_reported is True

    ble_client.poll_state.return_value = SubZeroData()
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert _accent_entity_id(hass) == entity_id
    assert coordinator.accent_light_reported is True

    ble_client.poll_state.return_value = SubZeroData(fields={"appliance_model": "IW30R"})
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert _accent_entity_id(hass) is None
    assert coordinator.accent_light_reported is False

    ble_client.poll_state.return_value = SubZeroData(
        fields={ACCENT_LIGHT_LEVEL: 0, "appliance_model": "IW30R"}
    )
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    entity_id = _accent_entity_id(hass)
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "off"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_push_that_includes_accent_light_adds_the_switch(
    hass: HomeAssistant, ble_client: MagicMock
) -> None:
    """A notification that carries the level creates the switch."""
    entry = mock_entry()
    entry.add_to_hass(hass)
    ble_client.poll_state.return_value = SubZeroData(
        fields={"appliance_model": "DEU2450C"}
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert _accent_entity_id(hass) is None
    assert entry.runtime_data.accent_light_reported is False

    entry.runtime_data._apply_push(SubZeroData(fields={ACCENT_LIGHT_LEVEL: 50}))
    await hass.async_block_till_done()
    entity_id = _accent_entity_id(hass)
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "on"

    entry.runtime_data._apply_push(SubZeroData(fields={"ref_door_ajar": False}))
    await hass.async_block_till_done()
    assert _accent_entity_id(hass) == entity_id
    assert entry.runtime_data.accent_light_reported is True
    await hass.config_entries.async_unload(entry.entry_id)
