"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_deck() -> MagicMock:
    """Fully mocked Stream Deck hardware object."""
    deck = MagicMock()
    deck.key_count.return_value = 15
    deck.key_image_format.return_value = {"size": (72, 72)}
    deck.deck_type.return_value = "Stream Deck MK.2"
    deck.get_serial_number.return_value = "TEST123456"
    return deck


@pytest.fixture
def basic_yaml() -> str:
    return """
device:
  brightness: 70
buttons:
  - key: 0
    label: "Press me"
    on_press:
      - type: set_state
        key: pressed
        value: true
  - key: 1
    label: "Toggle"
    on_press:
      - type: toggle
        key: active
        values: ["off", "on"]
"""


@pytest.fixture
def config_file(tmp_path: Path, basic_yaml: str) -> Path:
    f = tmp_path / "config.yaml"
    f.write_text(basic_yaml)
    return f
