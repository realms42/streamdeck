"""Tests for StreamDeckService with fully mocked hardware."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from streamdeck_service.config_loader import load_config
from streamdeck_service.models import StreamDeckConfig
from streamdeck_service.service import StreamDeckService

BASIC_YAML = """
device:
  brightness: 70
buttons:
  - key: 0
    label: "Press"
    on_press:
      - type: set_state
        key: x
        value: hello
  - key: 1
    label: "Toggle"
    on_press:
      - type: toggle
        key: active
        values: ["off", "on"]
"""

CHANGED_YAML = """
device:
  brightness: 70
buttons:
  - key: 0
    label: "Changed"
  - key: 1
    label: "Toggle"
    on_press:
      - type: toggle
        key: active
        values: ["off", "on"]
"""

BRIGHTNESS_CHANGED_YAML = """
device:
  brightness: 90
buttons:
  - key: 0
    label: "Press"
"""


def _pil_patch() -> patch:
    return patch(
        "StreamDeck.ImageHelpers.PILHelper.to_native_format",
        return_value=b"\x00" * 72 * 72 * 3,
    )


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    f = tmp_path / "config.yaml"
    f.write_text(BASIC_YAML)
    return f


@pytest.fixture
def service(config_file: Path, mock_deck: MagicMock) -> StreamDeckService:
    """Service with a pre-wired mock deck, bypassing _open_deck."""
    svc = StreamDeckService(config_file)
    svc._deck = mock_deck
    svc._config = load_config(config_file)
    return svc


# ---------------------------------------------------------------------------
# _apply_config_fresh
# ---------------------------------------------------------------------------


def test_fresh_sets_brightness(service: StreamDeckService, mock_deck: MagicMock) -> None:
    with _pil_patch():
        service._apply_config_fresh(service._config)  # type: ignore[arg-type]
    mock_deck.set_brightness.assert_called_with(70)


def test_fresh_renders_all_keys(service: StreamDeckService, mock_deck: MagicMock) -> None:
    with _pil_patch():
        service._apply_config_fresh(service._config)  # type: ignore[arg-type]
    # 15 keys total
    assert mock_deck.set_key_image.call_count == 15


def test_fresh_registers_key_callback(service: StreamDeckService, mock_deck: MagicMock) -> None:
    with _pil_patch():
        service._apply_config_fresh(service._config)  # type: ignore[arg-type]
    mock_deck.set_key_callback.assert_called_once_with(service._on_key_event)


# ---------------------------------------------------------------------------
# _apply_config_diff
# ---------------------------------------------------------------------------


def test_diff_only_rerenders_changed_keys(
    service: StreamDeckService, mock_deck: MagicMock, tmp_path: Path
) -> None:
    new_file = tmp_path / "new.yaml"
    new_file.write_text(CHANGED_YAML)
    new_cfg = load_config(new_file)

    with _pil_patch():
        service._apply_config_diff(service._config, new_cfg)  # type: ignore[arg-type]

    # Only key 0 changed
    assert mock_deck.set_key_image.call_count == 1
    key_idx = mock_deck.set_key_image.call_args[0][0]
    assert key_idx == 0


def test_diff_updates_brightness_when_changed(
    service: StreamDeckService, mock_deck: MagicMock, tmp_path: Path
) -> None:
    new_file = tmp_path / "bright.yaml"
    new_file.write_text(BRIGHTNESS_CHANGED_YAML)
    new_cfg = load_config(new_file)

    with _pil_patch():
        service._apply_config_diff(service._config, new_cfg)  # type: ignore[arg-type]

    mock_deck.set_brightness.assert_called_with(90)


def test_diff_identical_configs_no_rerenders(
    service: StreamDeckService, mock_deck: MagicMock, config_file: Path
) -> None:
    same_cfg = load_config(config_file)
    with _pil_patch():
        service._apply_config_diff(service._config, same_cfg)  # type: ignore[arg-type]
    assert mock_deck.set_key_image.call_count == 0


def test_diff_removed_key_blanked(
    service: StreamDeckService, mock_deck: MagicMock, tmp_path: Path
) -> None:
    new_file = tmp_path / "fewer.yaml"
    new_file.write_text("device:\n  brightness: 70\nbuttons:\n  - key: 1\n    label: X\n")
    new_cfg = load_config(new_file)

    with _pil_patch():
        service._apply_config_diff(service._config, new_cfg)  # type: ignore[arg-type]

    # key 0 removed + key 1 changed label = 2 re-renders
    assert mock_deck.set_key_image.call_count == 2


# ---------------------------------------------------------------------------
# _on_key_event
# ---------------------------------------------------------------------------


def test_key_press_dispatches_on_press(service: StreamDeckService) -> None:
    with _pil_patch():
        service._on_key_event(None, 0, True)
    assert service._state.get("x") == "hello"


def test_key_release_dispatches_on_release(service: StreamDeckService, config_file: Path) -> None:
    config_file.write_text(
        "buttons:\n"
        "  - key: 0\n"
        "    on_release:\n"
        "      - type: set_state\n"
        "        key: released\n"
        "        value: 'done'\n"
    )
    svc = StreamDeckService(config_file)
    svc._deck = service._deck
    svc._config = load_config(config_file)

    with _pil_patch():
        svc._on_key_event(None, 0, False)
    assert svc._state.get("released") == "done"


def test_key_press_on_undefined_key_is_noop(service: StreamDeckService) -> None:
    # key 99 not in config — should not raise
    service._on_key_event(None, 99, True)


def test_key_press_rerenders_key(service: StreamDeckService, mock_deck: MagicMock) -> None:
    with _pil_patch():
        service._on_key_event(None, 0, True)
    # set_key_image called once for the re-render
    assert mock_deck.set_key_image.call_count == 1


# ---------------------------------------------------------------------------
# _on_config_changed
# ---------------------------------------------------------------------------


def test_config_changed_valid_applies_diff(
    service: StreamDeckService,
    mock_deck: MagicMock,
    config_file: Path,
) -> None:
    # Write a new config with key 0 label changed
    config_file.write_text(CHANGED_YAML)
    with _pil_patch():
        service._on_config_changed()
    assert service._config is not None
    assert service._config.buttons[0].label == "Changed"


def test_config_changed_invalid_keeps_old(
    service: StreamDeckService,
    config_file: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    old_cfg = service._config
    config_file.write_text("device:\n  brightness: 9999\n")
    service._on_config_changed()
    assert service._config is old_cfg
    assert "Reload failed" in caplog.text


def test_config_changed_prunes_stale_state(service: StreamDeckService, config_file: Path) -> None:
    # Seed a state variable that won't exist in new config
    service._state.set("stale_var", "value")
    config_file.write_text("buttons:\n  - key: 0\n    label: New\n")
    with _pil_patch():
        service._on_config_changed()
    assert service._state.get("stale_var") is None


# ---------------------------------------------------------------------------
# _open_deck
# ---------------------------------------------------------------------------


def test_open_deck_by_index(tmp_path: Path, mock_deck: MagicMock) -> None:
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("device:\n  index: 0\n")
    svc = StreamDeckService(cfg_file)
    cfg = load_config(cfg_file)

    with patch("streamdeck_service.service.DeviceManager") as MockDM:
        MockDM.return_value.enumerate.return_value = [mock_deck]
        deck = svc._open_deck(cfg)  # type: ignore[arg-type]

    assert deck is mock_deck
    mock_deck.open.assert_called_once()
    mock_deck.reset.assert_called_once()


def test_open_deck_returns_none_when_empty(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("")
    svc = StreamDeckService(cfg_file)
    cfg = StreamDeckConfig()

    with patch("streamdeck_service.service.DeviceManager") as MockDM:
        MockDM.return_value.enumerate.return_value = []
        assert svc._open_deck(cfg) is None


def test_open_deck_by_serial(tmp_path: Path, mock_deck: MagicMock) -> None:
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("device:\n  serial: MYSERIAL\n")
    svc = StreamDeckService(cfg_file)
    cfg = load_config(cfg_file)

    mock_deck.get_serial_number.return_value = "MYSERIAL"
    other = MagicMock()
    other.get_serial_number.return_value = "OTHER"

    with patch("streamdeck_service.service.DeviceManager") as MockDM:
        MockDM.return_value.enumerate.return_value = [other, mock_deck]
        deck = svc._open_deck(cfg)  # type: ignore[arg-type]

    assert deck is mock_deck
    other.close.assert_called_once()


def test_open_deck_serial_not_found_falls_back_to_index(
    tmp_path: Path, mock_deck: MagicMock
) -> None:
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("device:\n  serial: NOTHERE\n  index: 0\n")
    svc = StreamDeckService(cfg_file)
    cfg = load_config(cfg_file)

    mock_deck.get_serial_number.return_value = "DIFFERENT"

    with patch("streamdeck_service.service.DeviceManager") as MockDM:
        MockDM.return_value.enumerate.return_value = [mock_deck]
        deck = svc._open_deck(cfg)  # type: ignore[arg-type]

    assert deck is mock_deck


# ---------------------------------------------------------------------------
# _set_brightness
# ---------------------------------------------------------------------------


def test_set_brightness_calls_deck_and_updates_config(
    service: StreamDeckService, mock_deck: MagicMock
) -> None:
    service._set_brightness(80)
    mock_deck.set_brightness.assert_called_with(80)
    assert service._config.device.brightness == 80  # type: ignore[union-attr]
