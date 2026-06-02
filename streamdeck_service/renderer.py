"""Render button visuals to native Stream Deck key images using Pillow."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .models import ButtonDef, StateAppearance
from .state_store import StateStore

log = logging.getLogger(__name__)

# Pillow font cache — avoids re-loading the same truetype file repeatedly.
# Keyed by (font spec, size); a None spec means "use the built-in defaults".
_font_cache: dict[tuple[str | None, int], ImageFont.ImageFont] = {}

# Scaled-icon cache — avoids re-reading and re-resizing the same image on every
# render. Keyed by (resolved path, mtime, size) so edits on disk invalidate it.
_icon_cache: dict[tuple[str, float, tuple[int, int]], Image.Image] = {}
_icon_cache_lock = threading.Lock()

# Common fonts to try when no explicit font is configured (or the configured
# one fails to load): a bundled-on-Linux face, then a common Windows face.
_FALLBACK_FONTS = ("DejaVuSans-Bold.ttf", "arial.ttf")


def _resolve_font_path(font: str | None, config_dir: Path) -> str | None:
    """Turn a configured font into something ``ImageFont.truetype`` accepts.

    A value that names a file relative to the config directory is resolved to an
    absolute path; anything else is passed through unchanged so Pillow can look
    it up as a system font name.
    """
    if not font:
        return None
    candidate = config_dir / font
    if candidate.is_file():
        return str(candidate)
    return font


def _load_font(size: int, font_spec: str | None = None) -> ImageFont.ImageFont:
    """Load a truetype font at *size*, falling back gracefully.

    Order: the explicit *font_spec* (if given) → bundled/common fonts → the
    Pillow built-in bitmap font.  Results are cached per (spec, size).
    """
    key = (font_spec, size)
    cached = _font_cache.get(key)
    if cached is not None:
        return cached

    candidates = [font_spec, *_FALLBACK_FONTS] if font_spec else list(_FALLBACK_FONTS)
    font: ImageFont.ImageFont | None = None
    for i, cand in enumerate(candidates):
        try:
            font = ImageFont.truetype(cand, size)
            break
        except OSError:
            if i == 0 and font_spec:
                log.warning("Could not load font %r; falling back to a default", font_spec)
    if font is None:
        font = ImageFont.load_default()

    _font_cache[key] = font
    return font


def _load_scaled_icon(icon_path: Path, size: tuple[int, int]) -> Image.Image | None:
    """Return *icon_path* opened as RGBA and resized to *size*, or None on error.

    Cached by (path, mtime, size); a changed mtime supersedes the stale entry so
    live edits to an icon take effect on the next render.
    """
    try:
        mtime = icon_path.stat().st_mtime
        cache_key = (str(icon_path), mtime, size)
        with _icon_cache_lock:
            cached = _icon_cache.get(cache_key)
        if cached is not None:
            return cached
        icon = Image.open(icon_path).convert("RGBA").resize(size, Image.LANCZOS)
    except Exception as exc:
        log.warning("Could not load icon %s: %s", icon_path, exc)
        return None

    with _icon_cache_lock:
        # Drop any superseded entry for this path so the cache can't grow
        # without bound as an icon is edited repeatedly.
        stale = [k for k in _icon_cache if k[0] == str(icon_path) and k[1] != mtime]
        for k in stale:
            del _icon_cache[k]
        _icon_cache[cache_key] = icon
    return icon


class EffectiveAppearance:
    __slots__ = ("background_color", "font", "font_size", "icon", "label", "text_color")

    def __init__(
        self,
        label: str | None,
        font_size: int,
        text_color: str,
        background_color: str,
        icon: str | None,
        font: str | None,
    ) -> None:
        self.label = label
        self.font_size = font_size
        self.text_color = text_color
        self.background_color = background_color
        self.icon = icon
        self.font = font


def _effective_appearance(
    btn: ButtonDef,
    state: StateStore,
    default_font: str | None = None,
) -> EffectiveAppearance:
    """Merge state-specific overrides onto the button's default appearance."""
    ea = EffectiveAppearance(
        label=btn.label,
        font_size=btn.font_size,
        text_color=btn.text_color,
        background_color=btn.background_color,
        icon=btn.icon,
        font=btn.font if btn.font is not None else default_font,
    )
    for var_name, mapping in btn.states.items():
        current_val = state.get(var_name)
        # Try exact match then string coercion
        override: StateAppearance | None = mapping.get(current_val)
        if override is None:
            override = mapping.get(str(current_val))
        if override is None:
            continue
        if override.label is not None:
            ea.label = override.label
        if override.font_size is not None:
            ea.font_size = override.font_size
        if override.text_color is not None:
            ea.text_color = override.text_color
        if override.background_color is not None:
            ea.background_color = override.background_color
        if override.icon is not None:
            ea.icon = override.icon
        if override.font is not None:
            ea.font = override.font
    return ea


def render_key(
    btn: ButtonDef,
    state: StateStore,
    key_size: tuple[int, int],
    config_dir: Path,
    default_font: str | None = None,
) -> Image.Image:
    """Produce a Pillow Image ready to send to the deck for *btn*."""
    ea = _effective_appearance(btn, state, default_font)
    w, h = key_size

    img = Image.new("RGB", (w, h), ea.background_color)

    # Draw icon if present
    if ea.icon:
        icon_img = _load_scaled_icon(config_dir / ea.icon, (w, h))
        if icon_img is not None:
            # Composite the (possibly transparent) icon over the background
            bg = Image.new("RGBA", (w, h), ea.background_color)
            bg.paste(icon_img, mask=icon_img)
            img = bg.convert("RGB")

    # Draw label text (multiline-aware, centered both ways)
    if ea.label:
        draw = ImageDraw.Draw(img)
        font = _load_font(ea.font_size, _resolve_font_path(ea.font, config_dir))
        bbox = draw.multiline_textbbox((0, 0), ea.label, font=font, align="center")
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        # Subtract the bbox origin so glyph side-bearings don't skew centering.
        x = (w - tw) // 2 - bbox[0]
        y = (h - th) // 2 - bbox[1]
        draw.multiline_text((x, y), ea.label, font=font, fill=ea.text_color, align="center")

    return img


def render_blank(key_size: tuple[int, int]) -> Image.Image:
    """Return a solid black image for unused keys."""
    return Image.new("RGB", key_size, "black")
