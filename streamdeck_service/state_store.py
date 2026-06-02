"""Runtime state store — a simple dict of named state variables.

Buttons read from the store to decide which visual to render and write to
it when actions execute.  The store fires a callback whenever a value
changes so the renderer knows which keys need updating.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)


class StateStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {}
        self._listeners: list[Callable[[str, Any], None]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            old = self._data.get(key)
            if old == value:
                return
            self._data[key] = value
        log.debug("State changed: %s = %r", key, value)
        for listener in self._listeners:
            try:
                listener(key, value)
            except Exception:
                log.exception("State listener raised an exception")

    def toggle(self, key: str, values: list[Any]) -> None:
        """Flip between two values.  Initialises to values[0] if key is unset."""
        with self._lock:
            current = self._data.get(key)
        try:
            idx = values.index(current)
            next_val = values[(idx + 1) % len(values)]
        except ValueError:
            next_val = values[0]
        self.set(key, next_val)

    def add_listener(self, fn: Callable[[str, Any], None]) -> None:
        self._listeners.append(fn)

    def merge(self, other: StateStore) -> None:
        """Copy values from *other* into self without firing listeners."""
        incoming = other.snapshot()  # read under other's own lock
        with self._lock:
            self._data.update(incoming)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data)

    def prune_to_keys(self, valid_keys: set[str]) -> None:
        """Remove state variables that are no longer referenced by any button."""
        with self._lock:
            for k in list(self._data):
                if k not in valid_keys:
                    log.debug("Pruning stale state key: %s", k)
                    del self._data[k]
