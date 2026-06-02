"""Tests for the action dispatcher."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from streamdeck_service.actions import dispatch
from streamdeck_service.models import (
    CommandAction,
    SetBrightnessAction,
    SetStateAction,
    ToggleAction,
)
from streamdeck_service.state_store import StateStore


@pytest.fixture
def state() -> StateStore:
    return StateStore()


@pytest.fixture
def brightness_cb() -> MagicMock:
    return MagicMock()


# ---------------------------------------------------------------------------
# set_state
# ---------------------------------------------------------------------------


def test_dispatch_set_state(state: StateStore, brightness_cb: MagicMock) -> None:
    action = SetStateAction(type="set_state", key="muted", value=True)
    dispatch(action, state, brightness_cb)
    assert state.get("muted") is True


def test_dispatch_set_state_string_value(state: StateStore, brightness_cb: MagicMock) -> None:
    action = SetStateAction(type="set_state", key="mode", value="active")
    dispatch(action, state, brightness_cb)
    assert state.get("mode") == "active"


# ---------------------------------------------------------------------------
# toggle
# ---------------------------------------------------------------------------


def test_dispatch_toggle_initialises(state: StateStore, brightness_cb: MagicMock) -> None:
    action = ToggleAction(type="toggle", key="x", values=["off", "on"])
    dispatch(action, state, brightness_cb)
    assert state.get("x") == "off"


def test_dispatch_toggle_cycles(state: StateStore, brightness_cb: MagicMock) -> None:
    action = ToggleAction(type="toggle", key="x", values=["off", "on"])
    dispatch(action, state, brightness_cb)
    dispatch(action, state, brightness_cb)
    assert state.get("x") == "on"


# ---------------------------------------------------------------------------
# set_brightness
# ---------------------------------------------------------------------------


def test_dispatch_set_brightness(state: StateStore, brightness_cb: MagicMock) -> None:
    action = SetBrightnessAction(type="set_brightness", value=50)
    dispatch(action, state, brightness_cb)
    brightness_cb.assert_called_once_with(50)


def test_dispatch_set_brightness_zero(state: StateStore, brightness_cb: MagicMock) -> None:
    action = SetBrightnessAction(type="set_brightness", value=0)
    dispatch(action, state, brightness_cb)
    brightness_cb.assert_called_once_with(0)


# ---------------------------------------------------------------------------
# command
# ---------------------------------------------------------------------------


def test_dispatch_command_launches_process(state: StateStore, brightness_cb: MagicMock) -> None:
    action = CommandAction(type="command", run="echo", args=["hello"])
    with patch("subprocess.Popen") as mock_popen:
        dispatch(action, state, brightness_cb)
    mock_popen.assert_called_once()
    cmd = mock_popen.call_args[0][0]
    assert cmd == ["echo", "hello"]


def test_dispatch_command_passes_kwargs(state: StateStore, brightness_cb: MagicMock) -> None:
    action = CommandAction(type="command", run="ls")
    with patch("subprocess.Popen") as mock_popen:
        dispatch(action, state, brightness_cb)
    _, kwargs = mock_popen.call_args
    assert kwargs.get("start_new_session") is True
    assert kwargs.get("stdout") is not None  # DEVNULL


def test_dispatch_command_shlex_splits_run(state: StateStore, brightness_cb: MagicMock) -> None:
    action = CommandAction(type="command", run="git commit -m 'test'")
    with patch("subprocess.Popen") as mock_popen:
        dispatch(action, state, brightness_cb)
    cmd = mock_popen.call_args[0][0]
    assert cmd[0] == "git"
    assert cmd[1] == "commit"


def test_dispatch_command_not_found_logs_error(
    state: StateStore,
    brightness_cb: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    action = CommandAction(type="command", run="no_such_binary_xyz")
    with patch("subprocess.Popen", side_effect=FileNotFoundError):
        dispatch(action, state, brightness_cb)
    assert "Command not found" in caplog.text


def test_dispatch_command_oserror_logs_error(
    state: StateStore,
    brightness_cb: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    action = CommandAction(type="command", run="echo")
    with patch("subprocess.Popen", side_effect=OSError("permission denied")):
        dispatch(action, state, brightness_cb)
    assert "Failed to launch" in caplog.text
