"""Tests for the Pillow-based key image renderer."""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from streamdeck_service import renderer
from streamdeck_service.models import ButtonDef
from streamdeck_service.renderer import (
    _load_font,
    _resolve_font_path,
    render_blank,
    render_key,
)
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


# ---------------------------------------------------------------------------
# Multi-line labels
# ---------------------------------------------------------------------------


def test_render_multiline_label(state: StateStore, tmp_path: Path) -> None:
    btn = ButtonDef.model_validate(
        {"key": 0, "label": "TOP\nBOT", "font_size": 16, "background_color": "black"}
    )
    img = render_key(btn, state, KEY_SIZE, tmp_path)
    assert img.size == KEY_SIZE
    # Two lines of text must paint pixels in both the top and bottom halves.
    px = img.load()
    top = any(px[x, y] != (0, 0, 0) for y in range(4, 30) for x in range(KEY_SIZE[0]))
    bottom = any(px[x, y] != (0, 0, 0) for y in range(42, 68) for x in range(KEY_SIZE[0]))
    assert top and bottom


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------


def test_resolve_font_path_prefers_local_file(tmp_path: Path) -> None:
    (tmp_path / "myfont.ttf").write_bytes(b"not-a-real-font")
    assert _resolve_font_path("myfont.ttf", tmp_path) == str(tmp_path / "myfont.ttf")
    # Not a local file → passed through for system-font lookup
    assert _resolve_font_path("Arial", tmp_path) == "Arial"
    assert _resolve_font_path(None, tmp_path) is None


def test_load_font_warns_and_falls_back_on_bad_font(
    caplog: pytest.LogCaptureFixture,
) -> None:
    renderer._font_cache.clear()
    font = _load_font(14, "definitely_not_installed_font_xyz.ttf")
    assert font is not None  # fell back to a default
    assert "Could not load font" in caplog.text


def test_load_font_is_cached() -> None:
    renderer._font_cache.clear()
    first = _load_font(18)
    second = _load_font(18)
    assert first is second  # same cached object


def test_button_font_override_used(state: StateStore, tmp_path: Path) -> None:
    # A per-button font that can't be loaded should warn but still render.
    btn = ButtonDef.model_validate(
        {"key": 0, "label": "Hi", "font": "no_such_font_abc.ttf", "background_color": "black"}
    )
    renderer._font_cache.clear()
    img = render_key(btn, state, KEY_SIZE, tmp_path)
    assert img.size == KEY_SIZE


def test_default_font_threaded_through(state: StateStore, tmp_path: Path) -> None:
    btn = ButtonDef.model_validate({"key": 0, "label": "Hi", "background_color": "black"})
    with patch("streamdeck_service.renderer._load_font", wraps=_load_font) as mock_load:
        render_key(btn, state, KEY_SIZE, tmp_path, default_font="Arial")
    # The device-level default font reaches the font loader.
    spec_args = [call.args[1] for call in mock_load.call_args_list if len(call.args) > 1]
    assert "Arial" in spec_args


# ---------------------------------------------------------------------------
# Icon scaling cache
# ---------------------------------------------------------------------------


def test_icon_cache_avoids_reopen(state: StateStore, tmp_path: Path) -> None:
    renderer._icon_cache.clear()
    Image.new("RGBA", (72, 72), (0, 128, 255, 255)).save(tmp_path / "icon.png")
    btn = ButtonDef.model_validate({"key": 0, "icon": "icon.png", "background_color": "black"})

    with patch("streamdeck_service.renderer.Image.open", wraps=Image.open) as mock_open:
        render_key(btn, state, KEY_SIZE, tmp_path)
        render_key(btn, state, KEY_SIZE, tmp_path)
    assert mock_open.call_count == 1  # second render served from cache


def test_icon_cache_invalidates_on_mtime_change(state: StateStore, tmp_path: Path) -> None:
    renderer._icon_cache.clear()
    icon_path = tmp_path / "icon.png"
    Image.new("RGBA", (72, 72), (0, 128, 255, 255)).save(icon_path)
    btn = ButtonDef.model_validate({"key": 0, "icon": "icon.png", "background_color": "black"})

    render_key(btn, state, KEY_SIZE, tmp_path)  # populate cache
    # Rewrite the icon and bump its mtime into the future to force invalidation.
    Image.new("RGBA", (72, 72), (255, 0, 0, 255)).save(icon_path)
    future = time.time() + 10
    os.utime(icon_path, (future, future))

    with patch("streamdeck_service.renderer.Image.open", wraps=Image.open) as mock_open:
        render_key(btn, state, KEY_SIZE, tmp_path)
    assert mock_open.call_count == 1  # reopened because mtime changed


def test_state_override_applies_every_field(tmp_path: Path) -> None:
    state = StateStore()
    Image.new("RGBA", (72, 72), (0, 0, 255, 255)).save(tmp_path / "s.png")
    btn = ButtonDef.model_validate(
        {
            "key": 0,
            "label": "D",
            "font_size": 14,
            "text_color": "white",
            "background_color": "black",
            "states": {
                "m": {
                    "on": {
                        "label": "O",
                        "font_size": 30,
                        "text_color": "#ff0000",
                        "background_color": "#00ff00",
                        "icon": "s.png",
                        "font": "Arial",
                    }
                }
            },
        }
    )
    state.set("m", "on")
    img = render_key(btn, state, KEY_SIZE, tmp_path)
    assert img.size == KEY_SIZE  # every override branch exercised without error


def test_load_font_ultimate_fallback_to_default() -> None:
    renderer._font_cache.clear()
    real_truetype = renderer.ImageFont.truetype

    def only_named_fonts_fail(font=None, *args, **kwargs):
        # Fail file/name lookups but let load_default()'s internal BytesIO load work.
        if isinstance(font, str):
            raise OSError("no font files available")
        return real_truetype(font, *args, **kwargs)

    with patch("streamdeck_service.renderer.ImageFont.truetype", side_effect=only_named_fonts_fail):
        font = _load_font(14, "anything.ttf")
    assert font is not None  # falls all the way back to ImageFont.load_default()
