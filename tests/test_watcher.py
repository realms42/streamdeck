"""Tests for the debounced config file watcher."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from streamdeck_service.watcher import ConfigWatcher, _ConfigHandler


def _event(src_path, *, is_directory: bool = False) -> SimpleNamespace:
    """A minimal stand-in for watchdog's FileSystemEvent."""
    return SimpleNamespace(is_directory=is_directory, src_path=src_path)


@pytest.fixture
def cfg_path(tmp_path: Path) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text("a: 1\n")
    return p


# ---------------------------------------------------------------------------
# _ConfigHandler — event filtering
# ---------------------------------------------------------------------------


def test_matching_event_fires_callback(cfg_path: Path) -> None:
    fired = threading.Event()
    handler = _ConfigHandler(cfg_path, fired.set, debounce=0.02)
    handler.on_any_event(_event(str(cfg_path)))
    assert fired.wait(2.0)


def test_directory_event_ignored(cfg_path: Path) -> None:
    fired = threading.Event()
    handler = _ConfigHandler(cfg_path, fired.set, debounce=0.02)
    handler.on_any_event(_event(str(cfg_path), is_directory=True))
    assert not fired.wait(0.2)


def test_other_file_event_ignored(cfg_path: Path, tmp_path: Path) -> None:
    fired = threading.Event()
    handler = _ConfigHandler(cfg_path, fired.set, debounce=0.02)
    handler.on_any_event(_event(str(tmp_path / "unrelated.yaml")))
    assert not fired.wait(0.2)


def test_empty_src_path_ignored(cfg_path: Path) -> None:
    fired = threading.Event()
    handler = _ConfigHandler(cfg_path, fired.set, debounce=0.02)
    handler.on_any_event(_event(""))  # must not raise
    assert not fired.wait(0.2)


def test_bytes_src_path_is_decoded(cfg_path: Path) -> None:
    """watchdog can hand us bytes on some platforms; it must still match."""
    fired = threading.Event()
    handler = _ConfigHandler(cfg_path, fired.set, debounce=0.02)
    handler.on_any_event(_event(os.fsencode(str(cfg_path))))
    assert fired.wait(2.0)


# ---------------------------------------------------------------------------
# _ConfigHandler — debounce behaviour
# ---------------------------------------------------------------------------


def test_rapid_events_coalesce_into_one_fire(cfg_path: Path) -> None:
    calls: list[int] = []
    handler = _ConfigHandler(cfg_path, lambda: calls.append(1), debounce=0.15)
    for _ in range(10):
        handler.on_any_event(_event(str(cfg_path)))
        time.sleep(0.005)
    time.sleep(0.4)
    assert calls == [1]  # only the last scheduled timer fired


def test_fire_swallows_callback_exception(cfg_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    def boom() -> None:
        raise RuntimeError("kaboom")

    handler = _ConfigHandler(cfg_path, boom, debounce=0.01)
    handler._fire()  # must not propagate
    assert "Reload callback raised" in caplog.text


# ---------------------------------------------------------------------------
# ConfigWatcher — real observer integration
# ---------------------------------------------------------------------------


def test_watcher_start_stop_is_clean(cfg_path: Path) -> None:
    w = ConfigWatcher(cfg_path, lambda: None, debounce=0.05)
    w.start()
    w.stop()  # should join cleanly without hanging


def test_watcher_detects_file_change(cfg_path: Path) -> None:
    fired = threading.Event()
    w = ConfigWatcher(cfg_path, fired.set, debounce=0.1)
    w.start()
    try:
        time.sleep(0.3)  # let the observer thread spin up
        cfg_path.write_text("a: 2\n")
        assert fired.wait(5.0)
    finally:
        w.stop()
