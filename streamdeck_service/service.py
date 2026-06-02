"""Main service — ties together device, config, renderer, watcher, and actions."""

from __future__ import annotations

import logging
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

from StreamDeck.DeviceManager import DeviceManager
from StreamDeck.Transport.Transport import TransportError

from .actions import dispatch
from .config_loader import load_config
from .models import AnyAction, ButtonDef, StreamDeckConfig
from .renderer import render_blank, render_key
from .state_store import StateStore
from .watcher import ConfigWatcher

log = logging.getLogger(__name__)


class StreamDeckService:
    def __init__(self, config_path: Path, retry: float = 0.0) -> None:
        self._config_path = config_path.resolve()
        self._config_dir = self._config_path.parent
        self._retry = retry
        self._deck = None
        self._config: StreamDeckConfig | None = None
        self._state = StateStore()
        self._render_lock = threading.Lock()
        self._watcher: ConfigWatcher | None = None
        self._running = False

        # Per-key hold-timer bookkeeping (for `on_hold` actions).
        self._hold_lock = threading.Lock()
        self._hold_timers: dict[int, threading.Timer] = {}
        self._hold_fired: set[int] = set()

        # Re-render reactively whenever a state variable a button depends on changes.
        self._state.add_listener(self._on_state_changed)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        self._setup_signals()

        cfg = load_config(self._config_path)
        if cfg is None:
            log.error("Cannot start: initial config is invalid. Fix the config and restart.")
            sys.exit(1)

        self._deck = self._open_deck_with_retry(cfg)
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

    def _open_deck_with_retry(self, cfg: StreamDeckConfig):
        """Open a deck, optionally polling every ``self._retry`` seconds until one appears."""
        deck = self._open_deck(cfg)
        if deck is not None or self._retry <= 0:
            return deck
        log.warning("No Stream Deck found; retrying every %.1f s (Ctrl-C to abort)", self._retry)
        while deck is None:
            time.sleep(self._retry)
            deck = self._open_deck(cfg)
        return deck

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
                    img = render_key(btn, self._state, key_size, self._config_dir, cfg.device.font)
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
            if key_idx not in new_map and key_idx < num_keys:
                changed_keys.append(key_idx)

        with self._render_lock:
            for key_idx in changed_keys:
                btn = new_map.get(key_idx)
                if btn:
                    img = render_key(
                        btn, self._state, key_size, self._config_dir, new_cfg.device.font
                    )
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
        self._state.prune_to_keys(self._referenced_state_keys(new_cfg))

        try:
            if old_cfg is not None:
                self._apply_config_diff(old_cfg, new_cfg)
            else:
                self._apply_config_fresh(new_cfg)
        except TransportError as exc:
            log.error("Deck communication error during reload: %s", exc)

    @staticmethod
    def _referenced_state_keys(cfg: StreamDeckConfig) -> set[str]:
        """Collect every state variable named by a button's `states` or actions."""
        keys: set[str] = set()
        for btn in cfg.buttons:
            keys.update(btn.states)
            for action in (*btn.on_press, *btn.on_release, *btn.on_hold):
                var = getattr(action, "key", None)
                if var is not None:
                    keys.add(var)
        return keys

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

        if pressed:
            self._dispatch_actions(btn.on_press, key_idx)
            self._arm_hold(btn, key_idx)
        else:
            # If a hold already fired for this press, swallow the release.
            if self._cancel_hold(key_idx):
                self._rerender_key(key_idx)
                return
            self._dispatch_actions(btn.on_release, key_idx)

        # Re-render this key in case state changed
        self._rerender_key(key_idx)

    def _dispatch_actions(self, actions: list[AnyAction], key_idx: int) -> None:
        for action in actions:
            try:
                dispatch(action, self._state, self._set_brightness)
            except Exception:
                log.exception("Error dispatching action on key %d", key_idx)

    def _on_state_changed(self, var_name: str, _value: Any) -> None:
        """Re-render every button whose `states` map references *var_name*."""
        cfg = self._config
        deck = self._deck
        if cfg is None or deck is None:
            return
        num_keys = deck.key_count()
        for btn in cfg.buttons:
            if btn.key < num_keys and var_name in btn.states:
                self._rerender_key(btn.key)

    # ------------------------------------------------------------------
    # Hold (long-press) handling
    # ------------------------------------------------------------------

    def _arm_hold(self, btn: ButtonDef, key_idx: int) -> None:
        """Start a timer that fires *btn*'s `on_hold` actions if the key stays down."""
        if not btn.on_hold:
            return
        timer = threading.Timer(btn.hold_seconds, self._fire_hold, args=(key_idx,))
        timer.daemon = True
        with self._hold_lock:
            existing = self._hold_timers.pop(key_idx, None)
            if existing is not None:
                existing.cancel()
            self._hold_fired.discard(key_idx)
            self._hold_timers[key_idx] = timer
        timer.start()

    def _cancel_hold(self, key_idx: int) -> bool:
        """Cancel a pending hold timer for *key_idx*; return True if it had fired."""
        with self._hold_lock:
            timer = self._hold_timers.pop(key_idx, None)
            fired = key_idx in self._hold_fired
            self._hold_fired.discard(key_idx)
        if timer is not None:
            timer.cancel()
        return fired

    def _fire_hold(self, key_idx: int) -> None:
        """Timer callback: run `on_hold` actions if the key is still held."""
        cfg = self._config
        if cfg is None:
            return
        btn = cfg.button_by_key(key_idx)
        if btn is None:
            return
        with self._hold_lock:
            # The release path pops the timer; if it's gone the press already ended.
            if self._hold_timers.pop(key_idx, None) is None:
                return
            self._hold_fired.add(key_idx)
        self._dispatch_actions(btn.on_hold, key_idx)
        self._rerender_key(key_idx)

    def _rerender_key(self, key_idx: int) -> None:
        cfg = self._config
        if cfg is None:
            return
        btn = cfg.button_by_key(key_idx)
        key_size = self._deck.key_image_format()["size"]
        with self._render_lock:
            img = (
                render_key(btn, self._state, key_size, self._config_dir, cfg.device.font)
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
        # Raising here unblocks signal.pause() and unwinds to run()'s finally.
        raise SystemExit(0)

    def _windows_wait(self) -> None:
        """Blocking wait for Windows where signal.pause() is unavailable."""
        while self._running:
            time.sleep(0.5)

    def _shutdown(self) -> None:
        log.info("Shutting down…")
        self._cancel_all_holds()
        if self._watcher:
            self._watcher.stop()
        if self._deck:
            try:
                self._deck.reset()
                self._deck.close()
            except TransportError:
                pass
        log.info("Bye.")

    def _cancel_all_holds(self) -> None:
        with self._hold_lock:
            timers = list(self._hold_timers.values())
            self._hold_timers.clear()
            self._hold_fired.clear()
        for timer in timers:
            timer.cancel()


def list_decks() -> list[dict[str, Any]]:
    """Enumerate connected Stream Decks, returning index/serial/type/key-count info."""
    decks = DeviceManager().enumerate()
    result: list[dict[str, Any]] = []
    for idx, d in enumerate(decks):
        d.open()
        try:
            result.append(
                {
                    "index": idx,
                    "serial": d.get_serial_number(),
                    "type": d.deck_type(),
                    "keys": d.key_count(),
                }
            )
        finally:
            d.close()
    return result
