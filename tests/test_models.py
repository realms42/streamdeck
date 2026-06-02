"""Tests for Pydantic config models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from streamdeck_service.models import (
    ButtonDef,
    CommandAction,
    DeviceConfig,
    SetBrightnessAction,
    SetStateAction,
    StateAppearance,
    StreamDeckConfig,
    ToggleAction,
)

# ---------------------------------------------------------------------------
# StreamDeckConfig
# ---------------------------------------------------------------------------


def test_empty_config_uses_defaults() -> None:
    cfg = StreamDeckConfig.model_validate({})
    assert cfg.device.brightness == 70
    assert cfg.device.index == 0
    assert cfg.buttons == []


def test_config_parses_buttons() -> None:
    cfg = StreamDeckConfig.model_validate({"buttons": [{"key": 0, "label": "Hi"}, {"key": 1}]})
    assert len(cfg.buttons) == 2
    assert cfg.buttons[0].label == "Hi"


def test_duplicate_key_raises() -> None:
    with pytest.raises(ValidationError, match="Duplicate button key"):
        StreamDeckConfig.model_validate({"buttons": [{"key": 0}, {"key": 0}]})


def test_button_by_key_found() -> None:
    cfg = StreamDeckConfig.model_validate({"buttons": [{"key": 3, "label": "X"}]})
    btn = cfg.button_by_key(3)
    assert btn is not None
    assert btn.label == "X"


def test_button_by_key_missing() -> None:
    cfg = StreamDeckConfig.model_validate({"buttons": [{"key": 0}]})
    assert cfg.button_by_key(99) is None


# ---------------------------------------------------------------------------
# DeviceConfig
# ---------------------------------------------------------------------------


def test_device_brightness_defaults() -> None:
    d = DeviceConfig()
    assert d.brightness == 70
    assert d.index == 0
    assert d.serial is None


def test_device_brightness_bounds() -> None:
    with pytest.raises(ValidationError):
        DeviceConfig(brightness=101)
    with pytest.raises(ValidationError):
        DeviceConfig(brightness=-1)


# ---------------------------------------------------------------------------
# ButtonDef
# ---------------------------------------------------------------------------


def test_button_appearance_defaults() -> None:
    btn = ButtonDef.model_validate({"key": 0})
    assert btn.font_size == 14
    assert btn.text_color == "white"
    assert btn.background_color == "black"
    assert btn.icon is None
    assert btn.on_press == []
    assert btn.on_release == []


def test_single_action_coerced_to_list() -> None:
    btn = ButtonDef.model_validate({"key": 0, "on_press": {"type": "command", "run": "echo"}})
    assert len(btn.on_press) == 1
    assert isinstance(btn.on_press[0], CommandAction)


def test_button_key_must_be_non_negative() -> None:
    with pytest.raises(ValidationError):
        ButtonDef.model_validate({"key": -1})


def test_state_appearance_map_parsed() -> None:
    btn = ButtonDef.model_validate(
        {
            "key": 0,
            "states": {
                "muted": {
                    "true": {"label": "Muted", "background_color": "#cc0000"},
                    "false": {"label": "Live"},
                }
            },
        }
    )
    assert "muted" in btn.states
    assert btn.states["muted"]["true"].label == "Muted"
    assert btn.states["muted"]["false"].label == "Live"


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def test_command_action_parses() -> None:
    a = CommandAction.model_validate({"type": "command", "run": "echo", "args": ["hi"]})
    assert a.run == "echo"
    assert a.args == ["hi"]


def test_command_action_args_optional() -> None:
    a = CommandAction.model_validate({"type": "command", "run": "ls"})
    assert a.args == []


def test_set_state_action() -> None:
    a = SetStateAction.model_validate({"type": "set_state", "key": "x", "value": 42})
    assert a.key == "x"
    assert a.value == 42


def test_toggle_action_requires_two_values() -> None:
    with pytest.raises(ValidationError):
        ToggleAction.model_validate({"type": "toggle", "key": "x", "values": ["only"]})


def test_toggle_action_rejects_three_values() -> None:
    with pytest.raises(ValidationError):
        ToggleAction.model_validate({"type": "toggle", "key": "x", "values": ["a", "b", "c"]})


def test_toggle_action_valid() -> None:
    a = ToggleAction.model_validate({"type": "toggle", "key": "x", "values": ["off", "on"]})
    assert a.values == ["off", "on"]


def test_set_brightness_bounds() -> None:
    with pytest.raises(ValidationError):
        SetBrightnessAction.model_validate({"type": "set_brightness", "value": 101})
    with pytest.raises(ValidationError):
        SetBrightnessAction.model_validate({"type": "set_brightness", "value": -1})


def test_set_brightness_valid() -> None:
    a = SetBrightnessAction.model_validate({"type": "set_brightness", "value": 75})
    assert a.value == 75


# ---------------------------------------------------------------------------
# StateAppearance
# ---------------------------------------------------------------------------


def test_state_appearance_all_none_by_default() -> None:
    sa = StateAppearance()
    assert sa.label is None
    assert sa.icon is None
    assert sa.background_color is None


def test_state_appearance_partial() -> None:
    sa = StateAppearance(label="Hi", background_color="red")
    assert sa.label == "Hi"
    assert sa.background_color == "red"
    assert sa.text_color is None
