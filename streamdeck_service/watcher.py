"""Debounced file-system watcher that calls a callback when the config changes."""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

log = logging.getLogger(__name__)

_DEBOUNCE_SECONDS = 0.5


class _ConfigHandler(FileSystemEventHandler):
    def __init__(
        self,
        config_path: Path,
        callback: Callable[[], None],
        debounce: float = _DEBOUNCE_SECONDS,
    ) -> None:
        super().__init__()
        self._config_path = config_path.resolve()
        self._callback = callback
        self._debounce = debounce
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        src_path = event.src_path
        if not src_path:
            return
        # watchdog may hand us bytes rather than str on some platforms.
        if isinstance(src_path, bytes):
            src_path = os.fsdecode(src_path)
        if Path(src_path).resolve() != self._config_path:
            return
        self._schedule()

    def _schedule(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        log.debug("Config change detected, triggering reload")
        try:
            self._callback()
        except Exception:
            log.exception("Reload callback raised an exception")


class ConfigWatcher:
    """Watch a single file for changes and call *callback* (debounced)."""

    def __init__(
        self,
        config_path: Path,
        callback: Callable[[], None],
        debounce: float = _DEBOUNCE_SECONDS,
    ) -> None:
        self._path = config_path.resolve()
        self._handler = _ConfigHandler(self._path, callback, debounce)
        self._observer = Observer()
        self._observer.schedule(self._handler, str(self._path.parent), recursive=False)

    def start(self) -> None:
        self._observer.start()
        log.debug("Watching %s for changes", self._path)

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join()
        log.debug("File watcher stopped")
