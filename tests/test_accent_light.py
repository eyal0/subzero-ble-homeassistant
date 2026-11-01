"""Accent light level tracking."""

from custom_components.subzero_ble.const import (
    ACCENT_LIGHT_DEFAULT_ON_LEVEL,
    ACCENT_LIGHT_LEVEL,
)
from custom_components.subzero_ble.coordinator import (
    SubZeroData,
    accent_light_is_on,
    accent_light_on_level,
)
from custom_components.subzero_ble.switch import SWITCH_DESCRIPTIONS


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
    description = next(
        item for item in SWITCH_DESCRIPTIONS if item.key == "accent_light"
    )
    off = _with_level(130).merge(SubZeroData(fields={ACCENT_LIGHT_LEVEL: 0}))

    assert description.property_key == ACCENT_LIGHT_LEVEL
    assert description.off_value == 0
    assert description.is_on_fn(off) is False
    assert description.on_value_fn is not None
    assert description.on_value_fn(off) == 130
    assert description.on_value_fn(SubZeroData()) == ACCENT_LIGHT_DEFAULT_ON_LEVEL
