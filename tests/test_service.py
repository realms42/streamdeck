"""Tests for StreamDeckService with fully mocked hardware."""

from __future__ import annotations

import signal
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from StreamDeck.Transport.Transport import TransportError

from streamdeck_service.config_loader import load_config
from streamdeck_service.models import ButtonDef, StreamDeckConfig
from streamdeck_service.service import StreamDeckService, list_decks

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


# ---------------------------------------------------------------------------
# run() / lifecycle
# ---------------------------------------------------------------------------


def test_run_exits_when_config_invalid(tmp_path: Path) -> None:
    bad = tmp_path / "config.yaml"
    bad.write_text("device:\n  brightness: 9999\n")
    svc = StreamDeckService(bad)
    with patch.object(svc, "_setup_signals"), pytest.raises(SystemExit) as exc:
        svc.run()
    assert exc.value.code == 1


def test_run_exits_when_no_deck(config_file: Path) -> None:
    svc = StreamDeckService(config_file)
    with (
        patch.object(svc, "_setup_signals"),
        patch.object(svc, "_open_deck_with_retry", return_value=None),
        pytest.raises(SystemExit) as exc,
    ):
        svc.run()
    assert exc.value.code == 1


def test_run_happy_path_blocks_then_shuts_down(config_file: Path, mock_deck: MagicMock) -> None:
    svc = StreamDeckService(config_file)
    with (
        patch.object(svc, "_setup_signals"),
        patch.object(svc, "_open_deck", return_value=mock_deck),
        patch("streamdeck_service.service.ConfigWatcher") as MockWatcher,
        patch(
            "streamdeck_service.service.signal.pause",
            create=True,
            side_effect=KeyboardInterrupt,
        ),
        _pil_patch(),
    ):
        svc.run()
    MockWatcher.return_value.start.assert_called_once()
    MockWatcher.return_value.stop.assert_called_once()
    mock_deck.reset.assert_called()
    mock_deck.close.assert_called_once()


# ---------------------------------------------------------------------------
# Signals & shutdown
# ---------------------------------------------------------------------------


def test_setup_signals_registers_handlers(config_file: Path) -> None:
    svc = StreamDeckService(config_file)
    with patch("streamdeck_service.service.signal.signal") as mock_signal:
        svc._setup_signals()
    assert mock_signal.call_count == 2  # SIGINT + SIGTERM


def test_setup_signals_swallows_errors(config_file: Path) -> None:
    svc = StreamDeckService(config_file)
    with patch("streamdeck_service.service.signal.signal", side_effect=ValueError):
        svc._setup_signals()  # must not raise


def test_handle_signal_raises_system_exit(service: StreamDeckService) -> None:
    service._running = True
    with pytest.raises(SystemExit) as exc:
        service._handle_signal(signal.SIGINT, None)
    assert exc.value.code == 0
    assert service._running is False


def test_windows_wait_returns_when_stopped(service: StreamDeckService) -> None:
    service._running = True

    def stop(_seconds: float) -> None:
        service._running = False

    with patch("streamdeck_service.service.time.sleep", side_effect=stop) as mock_sleep:
        service._windows_wait()
    mock_sleep.assert_called_once()


def test_shutdown_stops_watcher_and_resets_deck(
    service: StreamDeckService, mock_deck: MagicMock
) -> None:
    watcher = MagicMock()
    service._watcher = watcher
    service._shutdown()
    watcher.stop.assert_called_once()
    mock_deck.reset.assert_called()
    mock_deck.close.assert_called_once()


def test_shutdown_swallows_transport_error(
    service: StreamDeckService, mock_deck: MagicMock
) -> None:
    mock_deck.reset.side_effect = TransportError("boom")
    service._shutdown()  # must not raise


# ---------------------------------------------------------------------------
# _open_deck_with_retry
# ---------------------------------------------------------------------------


def test_open_deck_with_retry_polls_until_found(
    service: StreamDeckService, mock_deck: MagicMock
) -> None:
    service._retry = 0.01
    with (
        patch.object(service, "_open_deck", side_effect=[None, None, mock_deck]) as mock_open,
        patch("streamdeck_service.service.time.sleep") as mock_sleep,
    ):
        deck = service._open_deck_with_retry(service._config)  # type: ignore[arg-type]
    assert deck is mock_deck
    assert mock_open.call_count == 3
    assert mock_sleep.call_count == 2


def test_open_deck_with_retry_disabled_returns_immediately(service: StreamDeckService) -> None:
    service._retry = 0
    with patch.object(service, "_open_deck", return_value=None) as mock_open:
        assert service._open_deck_with_retry(service._config) is None  # type: ignore[arg-type]
    mock_open.assert_called_once()


# ---------------------------------------------------------------------------
# Reactive re-render on state change
# ---------------------------------------------------------------------------

REACTIVE_YAML = """
device:
  brightness: 70
buttons:
  - key: 0
    label: "Toggle"
    on_press:
      - type: toggle
        key: muted
        values: ["false", "true"]
  - key: 2
    label: "Mic"
    states:
      muted:
        "true":
          label: "Muted"
"""


def _wire(config_path: Path, mock_deck: MagicMock) -> StreamDeckService:
    svc = StreamDeckService(config_path)
    svc._deck = mock_deck
    svc._config = load_config(config_path)
    return svc


def test_state_change_rerenders_dependent_keys(tmp_path: Path, mock_deck: MagicMock) -> None:
    f = tmp_path / "config.yaml"
    f.write_text(REACTIVE_YAML)
    svc = _wire(f, mock_deck)

    with _pil_patch():
        svc._on_key_event(None, 0, True)

    rendered = {call.args[0] for call in mock_deck.set_key_image.call_args_list}
    assert 0 in rendered  # pressed key
    assert 2 in rendered  # dependent key re-rendered reactively


def test_state_change_noop_without_deck(service: StreamDeckService) -> None:
    service._deck = None
    service._on_state_changed("muted", "true")  # must not raise


# ---------------------------------------------------------------------------
# on_hold (long press)
# ---------------------------------------------------------------------------

HOLD_YAML = """
buttons:
  - key: 0
    label: "Hold"
    on_press:
      - type: set_state
        key: pressed
        value: true
    hold_seconds: 0.05
    on_hold:
      - type: set_state
        key: held
        value: true
    on_release:
      - type: set_state
        key: released
        value: true
"""


def test_on_hold_fires_after_threshold(tmp_path: Path, mock_deck: MagicMock) -> None:
    f = tmp_path / "config.yaml"
    f.write_text(HOLD_YAML)
    svc = _wire(f, mock_deck)
    with _pil_patch():
        svc._on_key_event(None, 0, True)
        time.sleep(0.25)  # let the hold timer fire
    assert svc._state.get("pressed") is True
    assert svc._state.get("held") is True


def test_hold_suppresses_release(tmp_path: Path, mock_deck: MagicMock) -> None:
    f = tmp_path / "config.yaml"
    f.write_text(HOLD_YAML)
    svc = _wire(f, mock_deck)
    with _pil_patch():
        svc._on_key_event(None, 0, True)
        time.sleep(0.25)  # hold fires
        svc._on_key_event(None, 0, False)  # release after hold
    assert svc._state.get("held") is True
    assert svc._state.get("released") is None  # release was swallowed


def test_quick_release_runs_release_not_hold(tmp_path: Path, mock_deck: MagicMock) -> None:
    f = tmp_path / "config.yaml"
    f.write_text(HOLD_YAML)
    svc = _wire(f, mock_deck)
    with _pil_patch():
        svc._on_key_event(None, 0, True)
        svc._on_key_event(None, 0, False)  # release before threshold
        time.sleep(0.2)  # timer would have fired by now
    assert svc._state.get("released") is True
    assert svc._state.get("held") is None  # hold was cancelled


def test_shutdown_cancels_pending_hold(tmp_path: Path, mock_deck: MagicMock) -> None:
    f = tmp_path / "config.yaml"
    f.write_text(HOLD_YAML)
    svc = _wire(f, mock_deck)
    with _pil_patch():
        svc._on_key_event(None, 0, True)
        svc._shutdown()
        time.sleep(0.2)
    assert svc._state.get("held") is None  # timer cancelled on shutdown


# ---------------------------------------------------------------------------
# _referenced_state_keys
# ---------------------------------------------------------------------------


def test_referenced_state_keys_spans_all_action_lists(tmp_path: Path) -> None:
    f = tmp_path / "config.yaml"
    f.write_text(
        "buttons:\n"
        "  - key: 0\n"
        "    states:\n"
        "      s_state: {}\n"
        "    on_press:\n"
        "      - {type: set_state, key: s_press, value: 1}\n"
        "    on_release:\n"
        "      - {type: set_state, key: s_release, value: 1}\n"
        "    on_hold:\n"
        "      - {type: toggle, key: s_hold, values: [a, b]}\n"
    )
    cfg = load_config(f)
    keys = StreamDeckService._referenced_state_keys(cfg)  # type: ignore[arg-type]
    assert keys == {"s_state", "s_press", "s_release", "s_hold"}


# ---------------------------------------------------------------------------
# list_decks
# ---------------------------------------------------------------------------


def test_list_decks_returns_info(mock_deck: MagicMock) -> None:
    with patch("streamdeck_service.service.DeviceManager") as MockDM:
        MockDM.return_value.enumerate.return_value = [mock_deck]
        decks = list_decks()
    assert decks == [{"index": 0, "serial": "TEST123456", "type": "Stream Deck MK.2", "keys": 15}]
    mock_deck.open.assert_called_once()
    mock_deck.close.assert_called_once()


def test_list_decks_empty() -> None:
    with patch("streamdeck_service.service.DeviceManager") as MockDM:
        MockDM.return_value.enumerate.return_value = []
        assert list_decks() == []


# ---------------------------------------------------------------------------
# Guard / error paths
# ---------------------------------------------------------------------------


def test_on_key_event_noop_without_config(service: StreamDeckService) -> None:
    service._config = None
    service._on_key_event(None, 0, True)  # cfg-None guard, must not raise


def test_rerender_key_noop_without_config(service: StreamDeckService) -> None:
    service._config = None
    service._rerender_key(0)  # must not raise


def test_dispatch_actions_logs_on_error(
    service: StreamDeckService, caplog: pytest.LogCaptureFixture
) -> None:
    with patch("streamdeck_service.service.dispatch", side_effect=RuntimeError("boom")):
        service._dispatch_actions([MagicMock()], 0)
    assert "Error dispatching action" in caplog.text


def test_open_deck_handles_transport_error(service: StreamDeckService) -> None:
    with patch("streamdeck_service.service.DeviceManager") as MockDM:
        MockDM.return_value.enumerate.side_effect = TransportError("nope")
        assert service._open_deck(service._config) is None  # type: ignore[arg-type]


def test_open_deck_index_out_of_range(tmp_path: Path, mock_deck: MagicMock) -> None:
    f = tmp_path / "config.yaml"
    f.write_text("device:\n  index: 5\n")
    svc = StreamDeckService(f)
    cfg = load_config(f)
    with patch("streamdeck_service.service.DeviceManager") as MockDM:
        MockDM.return_value.enumerate.return_value = [mock_deck]  # only index 0 exists
        assert svc._open_deck(cfg) is None  # type: ignore[arg-type]


def test_diff_skips_button_key_beyond_deck(
    service: StreamDeckService, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    new_file = tmp_path / "big.yaml"
    new_file.write_text("device:\n  brightness: 70\nbuttons:\n  - key: 20\n    label: X\n")
    new_cfg = load_config(new_file)
    with _pil_patch():
        service._apply_config_diff(service._config, new_cfg)  # type: ignore[arg-type]
    assert "exceeds deck key count" in caplog.text


def test_reload_applies_fresh_when_no_prior_config(tmp_path: Path, mock_deck: MagicMock) -> None:
    f = tmp_path / "config.yaml"
    f.write_text(BASIC_YAML)
    svc = StreamDeckService(f)
    svc._deck = mock_deck
    svc._config = None  # no prior config → fresh apply path on reload
    with _pil_patch():
        svc._on_config_changed()
    assert mock_deck.set_key_image.call_count == 15


def test_reload_handles_transport_error(
    service: StreamDeckService,
    mock_deck: MagicMock,
    config_file: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config_file.write_text(CHANGED_YAML)
    mock_deck.set_key_image.side_effect = TransportError("disconnect")
    with _pil_patch():
        service._on_config_changed()  # must not raise
    assert "Deck communication error" in caplog.text


# ---------------------------------------------------------------------------
# Hold timer internals
# ---------------------------------------------------------------------------


def test_arm_hold_replaces_existing_timer(service: StreamDeckService) -> None:
    btn = ButtonDef.model_validate(
        {"key": 0, "on_hold": [{"type": "set_state", "key": "h", "value": 1}]}
    )
    with patch("streamdeck_service.service.threading.Timer") as MockTimer:
        t1, t2 = MagicMock(), MagicMock()
        MockTimer.side_effect = [t1, t2]
        service._arm_hold(btn, 0)
        service._arm_hold(btn, 0)  # re-arm should cancel the first
    t1.cancel.assert_called_once()
    t1.start.assert_called_once()
    t2.start.assert_called_once()


def test_fire_hold_noop_without_config(service: StreamDeckService) -> None:
    service._config = None
    service._fire_hold(0)  # cfg-None guard


def test_fire_hold_noop_unknown_key(service: StreamDeckService) -> None:
    service._fire_hold(99)  # btn-None guard


def test_fire_hold_noop_when_no_timer_armed(tmp_path: Path, mock_deck: MagicMock) -> None:
    f = tmp_path / "config.yaml"
    f.write_text(HOLD_YAML)
    svc = _wire(f, mock_deck)
    svc._fire_hold(0)  # no armed timer → early return
    assert svc._state.get("held") is None
