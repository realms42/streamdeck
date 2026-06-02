"""Tests for the Pillow-based key image renderer."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from streamdeck_service.models import ButtonDef
from streamdeck_service.renderer import render_blank, render_key
from streamdeck_service.state_store import StateStore

KEY_SIZE = (72, 72)


@pytest.fixture
def state() -> StateStore:
    return StateStore()


# ---------------------------------------------------------------------------
# render_blank
# ---------------------------------------------------------------------------


def test_render_blank_size() -> None:
    img = render_blank(KEY_SIZE)
    assert img.size == KEY_SIZE


def test_render_blank_is_black() -> None:
    img = render_blank(KEY_SIZE)
    assert img.mode == "RGB"
    assert img.getpixel((0, 0)) == (0, 0, 0)


def test_render_blank_custom_size() -> None:
    img = render_blank((96, 96))
    assert img.size == (96, 96)


# ---------------------------------------------------------------------------
# render_key
# ---------------------------------------------------------------------------


def test_render_key_returns_correct_size(state: StateStore, tmp_path: Path) -> None:
    btn = ButtonDef.model_validate({"key": 0})
    img = render_key(btn, state, KEY_SIZE, tmp_path)
    assert img.size == KEY_SIZE
    assert img.mode == "RGB"


def test_render_key_background_color(state: StateStore, tmp_path: Path) -> None:
    btn = ButtonDef.model_validate({"key": 0, "background_color": "#ff0000"})
    img = render_key(btn, state, KEY_SIZE, tmp_path)
    # Corner pixel should be red (no label or icon there)
    r, g, b = img.getpixel((2, 2))
    assert r == 255
    assert g == 0
    assert b == 0


def test_render_key_with_label(state: StateStore, tmp_path: Path) -> None:
    btn = ButtonDef.model_validate({"key": 0, "label": "Hi", "background_color": "black"})
    img = render_key(btn, state, KEY_SIZE, tmp_path)
    assert img.size == KEY_SIZE
    # Image must differ from a blank black key (text was drawn)
    blank = render_blank(KEY_SIZE)
    assert img.tobytes() != blank.tobytes()


def test_render_key_empty_label_skips_text(state: StateStore, tmp_path: Path) -> None:
    btn = ButtonDef.model_validate({"key": 0, "label": "", "background_color": "#00ff00"})
    img = render_key(btn, state, KEY_SIZE, tmp_path)
    # Should be solid green (no text drawn for empty string)
    r, g, _b = img.getpixel((2, 2))
    assert g == 255
    assert r == 0


def test_render_key_with_icon(state: StateStore, tmp_path: Path) -> None:
    icon = Image.new("RGBA", (72, 72), (0, 128, 255, 255))
    icon_path = tmp_path / "icon.png"
    icon.save(icon_path)

    btn = ButtonDef.model_validate({"key": 0, "icon": "icon.png", "background_color": "black"})
    img = render_key(btn, state, KEY_SIZE, tmp_path)
    # Center pixel should be close to the icon color
    r, _g, b = img.getpixel((36, 36))
    assert b > r  # blue channel dominates


def test_render_key_missing_icon_is_graceful(
    state: StateStore, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    btn = ButtonDef.model_validate({"key": 0, "background_color": "#0000ff"})
    btn.icon = "does_not_exist.png"  # bypass icon path check
    img = render_key(btn, state, KEY_SIZE, tmp_path)
    # Should still produce a valid image (falls back to background)
    assert img.size == KEY_SIZE
    assert "Could not load icon" in caplog.text


# ---------------------------------------------------------------------------
# State overrides
# ---------------------------------------------------------------------------


def test_state_override_changes_background(tmp_path: Path) -> None:
    state = StateStore()
    btn = ButtonDef.model_validate(
        {
            "key": 0,
            "background_color": "#00ff00",
            "states": {
                "active": {
                    "true": {"background_color": "#ff0000"},
                }
            },
        }
    )
    img_default = render_key(btn, state, KEY_SIZE, tmp_path)
    state.set("active", "true")
    img_active = render_key(btn, state, KEY_SIZE, tmp_path)

    # Images must differ (different background colors)
    assert img_default.getpixel((2, 2)) != img_active.getpixel((2, 2))


def test_state_override_changes_label(tmp_path: Path) -> None:
    state = StateStore()
    btn = ButtonDef.model_validate(
        {
            "key": 0,
            "label": "OFF",
            "background_color": "black",
            "states": {
                "on": {
                    "true": {"label": "ON"},
                }
            },
        }
    )
    img_off = render_key(btn, state, KEY_SIZE, tmp_path)
    state.set("on", "true")
    img_on = render_key(btn, state, KEY_SIZE, tmp_path)
    # Both images render something; they should differ
    assert img_off.tobytes() != img_on.tobytes()


def test_state_no_matching_value_uses_default(tmp_path: Path) -> None:
    state = StateStore()
    state.set("level", "unknown_value")
    btn = ButtonDef.model_validate(
        {
            "key": 0,
            "background_color": "#0000ff",
            "states": {
                "level": {
                    "high": {"background_color": "#ff0000"},
                }
            },
        }
    )
    img = render_key(btn, state, KEY_SIZE, tmp_path)
    # No matching state value → default blue background
    r, _g, b = img.getpixel((2, 2))
    assert b == 255
    assert r == 0
