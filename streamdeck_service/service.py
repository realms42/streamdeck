"""Main service — ties together device, config, renderer, watcher, and actions."""

from __future__ import annotations

import logging
import signal
import sys
import threading
from pathlib import Path

from StreamDeck.DeviceManager import DeviceManager
from StreamDeck.Transport.Transport import TransportError

from .actions import dispatch
from .config_loader import load_config
from .models import StreamDeckConfig
from .renderer import render_blank, render_key
from .state_store import StateStore
from .watcher import ConfigWatcher

log = logging.getLogger(__name__)


class StreamDeckService:
    def __init__(self, config_path: Path) -> None:
        self._config_path = config_path.resolve()
        self._config_dir = self._config_path.parent
        self._deck = None
        self._config: StreamDeckConfig | None = None
        self._state = StateStore()
        self._render_lock = threading.Lock()
        self._watcher: ConfigWatcher | None = None
        self._running = False

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        self._setup_signals()

        cfg = load_config(self._config_path)
        if cfg is None:
            log.error("Cannot start: initial config is invalid. Fix the config and restart.")
            sys.exit(1)

        self._deck = self._open_deck(cfg)
        if self._deck is None:
            log.error("No Stream Deck found. Connect a device and restart.")
            sys.exit(1)

        self._config = cfg
        self._apply_config_fresh(cfg)

        self._watcher = ConfigWatcher(self._config_path, self._on_config_changed)
        self._watcher.start()

        self._running = True
        log.info("Service running. Press Ctrl-C to quit.")
        try:
            # The stream deck library's read loop runs on a daemon thread.
            # Block the main thread here so the process stays alive.
            signal.pause() if hasattr(signal, "pause") else self._windows_wait()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            self._shutdown()

    # ------------------------------------------------------------------
    # Device management
    # ------------------------------------------------------------------

    def _open_deck(self, cfg: StreamDeckConfig):
        try:
            decks = DeviceManager().enumerate()
        except TransportError as exc:
            log.error("Transport error while enumerating decks: %s", exc)
            return None

        if not decks:
            return None

        deck = None
        if cfg.device.serial:
            for d in decks:
                d.open()
                if d.get_serial_number() == cfg.device.serial:
                    deck = d
                    break
                d.close()
            if deck is None:
                log.warning(
                    "No deck with serial %s found; falling back to index %d",
                    cfg.device.serial,
                    cfg.device.index,
                )
        if deck is None:
            idx = cfg.device.index
            if idx >= len(decks):
                log.error("Device index %d out of range (found %d deck(s))", idx, len(decks))
                return None
            deck = decks[idx]
            deck.open()

        deck.reset()
        log.info(
            "Opened deck: %s  serial=%s  keys=%d",
            deck.deck_type(),
            deck.get_serial_number(),
            deck.key_count(),
        )
        return deck

    # ------------------------------------------------------------------
    # Config application
    # ------------------------------------------------------------------

    def _apply_config_fresh(self, cfg: StreamDeckConfig) -> None:
        """Render all buttons and set brightness from scratch."""
        self._deck.set_brightness(cfg.device.brightness)
        key_size = self._deck.key_image_format()["size"]
        num_keys = self._deck.key_count()

        with self._render_lock:
            for key_idx in range(num_keys):
                btn = cfg.button_by_key(key_idx)
                if btn:
                    img = render_key(btn, self._state, key_size, self._config_dir)
                else:
                    img = render_blank(key_size)
                self._set_key_image(key_idx, img)

        self._deck.set_key_callback(self._on_key_event)
        log.debug("All %d keys rendered", num_keys)

    def _apply_config_diff(self, old_cfg: StreamDeckConfig, new_cfg: StreamDeckConfig) -> None:
        """Re-render only keys whose definition changed; update brightness if needed."""
        if new_cfg.device.brightness != old_cfg.device.brightness:
            self._deck.set_brightness(new_cfg.device.brightness)

        key_size = self._deck.key_image_format()["size"]
        num_keys = self._deck.key_count()

        old_map = {btn.key: btn for btn in old_cfg.buttons}
        new_map = {btn.key: btn for btn in new_cfg.buttons}

        changed_keys: list[int] = []

        # Keys that are new or changed
        for key_idx, new_btn in new_map.items():
            if key_idx >= num_keys:
                log.warning(
                    "Button key %d exceeds deck key count %d — skipping",
                    key_idx,
                    num_keys,
                )
                continue
            old_btn = old_map.get(key_idx)
            if old_btn is None or old_btn.model_dump() != new_btn.model_dump():
                changed_keys.append(key_idx)

        # Keys that were removed
        for key_idx in old_map:
            if key_idx not in new_map:
                changed_keys.append(key_idx)

        with self._render_lock:
            for key_idx in changed_keys:
                btn = new_map.get(key_idx)
                if btn:
                    img = render_key(btn, self._state, key_size, self._config_dir)
                else:
                    img = render_blank(key_size)
                self._set_key_image(key_idx, img)

        if changed_keys:
            log.info("Reloaded config: re-rendered keys %s", changed_keys)
        else:
            log.info("Reloaded config: no visual changes")

    def _set_key_image(self, key_idx: int, img) -> None:
        """Convert a Pillow image to the deck's native format and push it."""
        from StreamDeck.ImageHelpers import PILHelper

        native = PILHelper.to_native_format(self._deck, img)
        self._deck.set_key_image(key_idx, native)

    # ------------------------------------------------------------------
    # Live reload
    # ------------------------------------------------------------------

    def _on_config_changed(self) -> None:
        log.info("Config file changed, reloading…")
        new_cfg = load_config(self._config_path)
        if new_cfg is None:
            log.warning("Reload failed — keeping current config")
            return

        old_cfg = self._config
        self._config = new_cfg

        # Preserve state variables that still exist in the new config
        new_state_keys: set[str] = set()
        for btn in new_cfg.buttons:
            for var_name in btn.states:
                new_state_keys.add(var_name)
            for action in btn.on_press + btn.on_release:
                if hasattr(action, "key"):
                    new_state_keys.add(action.key)
        self._state.prune_to_keys(new_state_keys)

        try:
            if old_cfg is not None:
                self._apply_config_diff(old_cfg, new_cfg)
            else:
                self._apply_config_fresh(new_cfg)
        except TransportError as exc:
            log.error("Deck communication error during reload: %s", exc)

    # ------------------------------------------------------------------
    # Key press handling
    # ------------------------------------------------------------------

    def _on_key_event(self, _deck, key_idx: int, pressed: bool) -> None:
        cfg = self._config
        if cfg is None:
            return
        btn = cfg.button_by_key(key_idx)
        if btn is None:
            return

        actions = btn.on_press if pressed else btn.on_release
        for action in actions:
            try:
                dispatch(action, self._state, self._set_brightness)
            except Exception:
                log.exception("Error dispatching action on key %d", key_idx)

        # Re-render this key in case state changed
        self._rerender_key(key_idx)

    def _rerender_key(self, key_idx: int) -> None:
        cfg = self._config
        if cfg is None:
            return
        btn = cfg.button_by_key(key_idx)
        key_size = self._deck.key_image_format()["size"]
        with self._render_lock:
            img = (
                render_key(btn, self._state, key_size, self._config_dir)
                if btn
                else render_blank(key_size)
            )
            self._set_key_image(key_idx, img)

    def _set_brightness(self, value: int) -> None:
        self._deck.set_brightness(value)
        if self._config:
            # Reflect the new brightness in the live config object (not persisted)
            self._config.device.brightness = value

    # ------------------------------------------------------------------
    # Signals & shutdown
    # ------------------------------------------------------------------

    def _setup_signals(self) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, self._handle_signal)
            except (OSError, ValueError):
                pass  # signal handling not available in all contexts

    def _handle_signal(self, signum: int, frame) -> None:
        log.info("Received signal %d, shutting down…", signum)
        self._running = False
        # Unblock signal.pause() on Unix
        if hasattr(signal, "pthread_kill") and hasattr(threading, "main_thread"):
            pass  # signal.pause() will return after the handler
        raise SystemExit(0)

    def _windows_wait(self) -> None:
        """Blocking wait for Windows where signal.pause() is unavailable."""
        import time

        while self._running:
            time.sleep(0.5)

    def _shutdown(self) -> None:
        log.info("Shutting down…")
        if self._watcher:
            self._watcher.stop()
        if self._deck:
            try:
                self._deck.reset()
                self._deck.close()
            except TransportError:
                pass
        log.info("Bye.")
