"""Render button visuals to native Stream Deck key images using Pillow."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from .models import ButtonDef, StateAppearance, StreamDeckConfig
from .state_store import StateStore

log = logging.getLogger(__name__)

# Pillow font cache — avoids re-loading the same truetype file repeatedly
_font_cache: dict[Tuple[Optional[str], int], ImageFont.ImageFont] = {}


def _load_font(size: int) -> ImageFont.ImageFont:
    key = (None, size)
    if key not in _font_cache:
        try:
            # Try a common system font first; fall back to the Pillow default
            _font_cache[key] = ImageFont.truetype("DejaVuSans-Bold.ttf", size)
        except OSError:
            try:
                _font_cache[key] = ImageFont.truetype("arial.ttf", size)
            except OSError:
                _font_cache[key] = ImageFont.load_default()
    return _font_cache[key]


def _effective_appearance(
    btn: ButtonDef,
    state: StateStore,
) -> "EffectiveAppearance":
    """Merge state-specific overrides onto the button's default appearance."""
    ea = EffectiveAppearance(
        label=btn.label,
        font_size=btn.font_size,
        text_color=btn.text_color,
        background_color=btn.background_color,
        icon=btn.icon,
    )
    for var_name, mapping in btn.states.items():
        current_val = state.get(var_name)
        # Try exact match then string coercion
        override: Optional[StateAppearance] = mapping.get(current_val)
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
    return ea


class EffectiveAppearance:
    __slots__ = ("label", "font_size", "text_color", "background_color", "icon")

    def __init__(
        self,
        label: Optional[str],
        font_size: int,
        text_color: str,
        background_color: str,
        icon: Optional[str],
    ) -> None:
        self.label = label
        self.font_size = font_size
        self.text_color = text_color
        self.background_color = background_color
        self.icon = icon


def render_key(
    btn: ButtonDef,
    state: StateStore,
    key_size: Tuple[int, int],
    config_dir: Path,
) -> Image.Image:
    """Produce a Pillow Image ready to send to the deck for *btn*."""
    ea = _effective_appearance(btn, state)
    w, h = key_size

    img = Image.new("RGB", (w, h), ea.background_color)

    # Draw icon if present
    if ea.icon:
        icon_path = config_dir / ea.icon
        try:
            icon_img = Image.open(icon_path).convert("RGBA").resize((w, h), Image.LANCZOS)
            # Composite icon over background
            bg = Image.new("RGBA", (w, h), ea.background_color)
            bg.paste(icon_img, mask=icon_img)
            img = bg.convert("RGB")
        except Exception as exc:
            log.warning("Could not load icon %s: %s", icon_path, exc)

    # Draw label text
    if ea.label:
        draw = ImageDraw.Draw(img)
        font = _load_font(ea.font_size)
        # Center the text
        try:
            bbox = draw.textbbox((0, 0), ea.label, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
        except AttributeError:
            # Older Pillow fallback
            tw, th = draw.textsize(ea.label, font=font)  # type: ignore[attr-defined]
        x = (w - tw) // 2
        y = (h - th) // 2
        draw.text((x, y), ea.label, font=font, fill=ea.text_color)

    return img


def render_blank(key_size: Tuple[int, int]) -> Image.Image:
    """Return a solid black image for unused keys."""
    return Image.new("RGB", key_size, "black")
