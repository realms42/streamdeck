"""Tests for YAML config loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from streamdeck_service.config_loader import load_config

VALID_YAML = """
device:
  brightness: 80
buttons:
  - key: 0
    label: "Test"
  - key: 1
    label: "Other"
"""


def test_load_valid_config(tmp_path: Path) -> None:
    f = tmp_path / "config.yaml"
    f.write_text(VALID_YAML)
    cfg = load_config(f)
    assert cfg is not None
    assert cfg.device.brightness == 80
    assert len(cfg.buttons) == 2


def test_load_missing_file(tmp_path: Path) -> None:
    assert load_config(tmp_path / "nonexistent.yaml") is None


def test_load_empty_file(tmp_path: Path) -> None:
    f = tmp_path / "empty.yaml"
    f.write_text("")
    assert load_config(f) is None


def test_load_invalid_yaml(tmp_path: Path) -> None:
    f = tmp_path / "bad.yaml"
    f.write_text("key: {unclosed bracket")
    assert load_config(f) is None


def test_load_brightness_out_of_range(tmp_path: Path) -> None:
    f = tmp_path / "bad_schema.yaml"
    f.write_text("device:\n  brightness: 999\n")
    assert load_config(f) is None


def test_load_duplicate_keys(tmp_path: Path) -> None:
    f = tmp_path / "dup.yaml"
    f.write_text("buttons:\n  - key: 0\n  - key: 0\n")
    assert load_config(f) is None


def test_load_missing_icon(tmp_path: Path) -> None:
    f = tmp_path / "config.yaml"
    f.write_text("buttons:\n  - key: 0\n    icon: missing.png\n")
    assert load_config(f) is None


def test_load_existing_icon(tmp_path: Path) -> None:
    (tmp_path / "icon.png").write_bytes(b"fake-png")
    f = tmp_path / "config.yaml"
    f.write_text("buttons:\n  - key: 0\n    icon: icon.png\n")
    cfg = load_config(f)
    assert cfg is not None
    assert cfg.buttons[0].icon == "icon.png"


def test_load_missing_state_icon(tmp_path: Path) -> None:
    f = tmp_path / "config.yaml"
    f.write_text(
        "buttons:\n"
        "  - key: 0\n"
        "    states:\n"
        "      active:\n"
        "        'true':\n"
        "          icon: missing_state.png\n"
    )
    assert load_config(f) is None


def test_load_existing_state_icon(tmp_path: Path) -> None:
    (tmp_path / "state_icon.png").write_bytes(b"fake")
    f = tmp_path / "config.yaml"
    f.write_text(
        "buttons:\n"
        "  - key: 0\n"
        "    states:\n"
        "      active:\n"
        "        'true':\n"
        "          icon: state_icon.png\n"
    )
    cfg = load_config(f)
    assert cfg is not None


def test_load_single_action_shorthand(tmp_path: Path) -> None:
    f = tmp_path / "config.yaml"
    f.write_text("buttons:\n  - key: 0\n    on_press:\n      type: command\n      run: echo\n")
    cfg = load_config(f)
    assert cfg is not None
    assert len(cfg.buttons[0].on_press) == 1


def test_load_logs_validation_error(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    f = tmp_path / "config.yaml"
    f.write_text("device:\n  brightness: 999\n")
    load_config(f)
    assert "validation failed" in caplog.text.lower()


def test_load_defaults_when_device_omitted(tmp_path: Path) -> None:
    f = tmp_path / "config.yaml"
    f.write_text("buttons:\n  - key: 0\n")
    cfg = load_config(f)
    assert cfg is not None
    assert cfg.device.brightness == 70
