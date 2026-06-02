# Stream Deck Service — CLAUDE.md

## Project overview

A Python daemon that drives an Elgato Stream Deck from a YAML config file.
Key behaviours: live reload on config change, per-button state machine, four
action types, clean shutdown. Runs on Linux and Windows with identical code.

---

## Architecture & design decisions

### Module layout

```
streamdeck_service/
  models.py         Pydantic v2 schema — single source of truth for config shape
  config_loader.py  YAML → Pydantic → icon-path checks → human-readable errors
  state_store.py    Thread-safe {str: Any} store; fires listeners on change
  renderer.py       Pillow: merges state overrides → draws icon + label → Image
                    (font + scaled-icon caches; multiline-centered labels)
  actions.py        dispatch() — pure function, no hardware reference
  watcher.py        Debounced (configurable) watchdog observer
  service.py        Ties everything together; owns the deck handle.
                    Reactive re-render listener, on_hold timers, list_decks()
  __main__.py       CLI entry point (--config, --log-level, --retry, --list-decks)
```

### Key decisions

- **Pydantic v2 discriminated union** for actions — `type: Literal[...]` on each
  action model means Pydantic auto-selects the right class; no manual parsing.
- **`AnyAction` type alias** (`CommandAction | SetStateAction | ...`) used as the
  field type for `on_press` / `on_release` lists.
- **State store listeners** fire synchronously after every `set()` call. The
  service registers `_on_state_changed`, which re-renders every button whose
  `states` map references the changed variable (reactive re-render).
- **Config diff on reload** — `model_dump()` equality per button key; only changed
  or removed keys are re-rendered. Brightness updated separately.
- **`PILHelper.to_native_format` imported lazily** inside `_set_key_image` to
  avoid a top-level import of a hardware-dependent module during testing.
- **`_open_deck` returns `Any | None`** — the `streamdeck` library's deck type
  lives deep in its package hierarchy; using `Any` avoids a brittle import chain.
- **`signal.pause()` on Unix / polling loop on Windows** — `signal.pause()` is
  not available on Windows, so `_windows_wait()` polls `self._running` every 0.5 s.
- **Debounce at 0.5 s** — editors (vim, VS Code) often trigger multiple inotify
  events per save; the timer is cancelled and restarted on each event so only one
  reload fires. The interval is injectable (`ConfigWatcher(..., debounce=)`) so
  tests can run it fast. `event.src_path` is `os.fsdecode`d in case watchdog
  hands back bytes.
- **`on_hold` via `threading.Timer`** — on key-down a per-key timer is armed; if
  the key is released first the timer is cancelled and `on_release` runs, otherwise
  the timer fires `on_hold` and the subsequent `on_release` is swallowed. Timer
  bookkeeping is guarded by `_hold_lock`; `_shutdown` cancels any pending timers.
- **Font resolution** — `device.font` → `button.font` → state-override `font`,
  each a file path (resolved relative to the config dir) or a system font name.
  Loaded fonts are cached by `(spec, size)`; scaled icons by `(path, mtime, size)`.
- **`--retry N`** — `_open_deck_with_retry` polls every N seconds until a deck
  appears (default 0 = exit immediately if none found).
- **`start_new_session=True` in Popen** — detaches the child process from the
  service's process group so it outlives the service and doesn't receive SIGINT.
- **Python 3.10+ minimum** — enables `X | Y` union syntax, `match` (unused so far),
  and `list[X]` / `dict[K, V]` in annotations without `from __future__`.

### Config schema (YAML)

```
device:
  serial: str | null     # preferred selector
  index: int (default 0) # fallback
  brightness: 0-100
  font: str | null       # default font (file path or system name) for all buttons

buttons:
  - key: int             # physical key index
    label: str | null    # "\n" → multiple, centered lines
    font_size: int (14)
    text_color: str ("white")
    background_color: str ("black")
    icon: str | null     # path relative to config file
    font: str | null     # per-button font override
    hold_seconds: float (0.6, >0)   # press duration before on_hold fires
    states:
      <var_name>:
        <value>:          # merged over defaults when store[var_name] == value
          label / font_size / text_color / background_color / icon / font
    on_press: [Action]   # single dict also accepted (coerced to list)
    on_release: [Action]
    on_hold: [Action]    # fired after hold_seconds; suppresses that release
```

Action types: `command`, `set_state`, `toggle`, `set_brightness`.

---

## Test suite

```
tests/
  conftest.py          mock_deck, basic_yaml, config_file fixtures
  test_models.py       Pydantic validation, bounds, coercion
  test_config_loader.py YAML load, icon path checks, error logging
  test_state_store.py  get/set/toggle/prune/merge, listeners, thread safety
  test_actions.py      dispatch(), Popen mocking, error/unknown-action paths
  test_renderer.py     pixel-level colour assertions, state overrides, icon load,
                       multiline labels, font fallback/cache, icon cache
  test_watcher.py      _ConfigHandler debounce/filtering (incl. bytes path),
                       real-Observer integration
  test_service.py      fresh render, config diff, key events, reload, open_deck,
                       run()/signals/shutdown, retry, reactive render, on_hold,
                       list_decks
```

Run: `python -m pytest tests/ --cov=streamdeck_service --cov-report=term-missing`

Coverage: 100%. The blocking `run()` loop and `_windows_wait` are exercised by
mocking `signal.pause`/`time.sleep`; `ConfigWatcher` has a real-observer test plus
deterministic handler tests; `on_hold` timers use short, injectable durations.

---

## CI

`.github/workflows/ci.yml` — two jobs on every push / PR against `main`:

| Job | Steps |
|-----|-------|
| `lint` | `ruff check` + `ruff format --check` |
| `test` | `pytest --cov --cov-fail-under=80` on Python 3.10, 3.11, 3.12 × {ubuntu, windows} |

The `test` step pins `shell: bash` so the backslash line-continuations in the
`pytest` command work on Windows too — Windows runners default to PowerShell,
where `\` is not a line continuation (and bash ships on all GitHub runners).

Ruff config in `pyproject.toml`: `select = ["E","F","I","UP","B","RUF"]`,
`line-length = 100`, `ignore = ["RUF012"]` (Pydantic Field pattern).

---

## OS setup

### Linux

```bash
sudo apt install libhidapi-hidraw0 libhidapi-libusb0
sudo cp 70-streamdeck.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
sudo usermod -aG plugdev $USER   # re-login required
```

### Windows

```powershell
pip install hidapi   # bundles the DLL; no system install needed
```

---

## Leftover tasks / follow-up ideas

### Completed

- [x] **`watcher.py` test coverage** — `test_watcher.py` adds deterministic
  `_ConfigHandler` tests (event filtering, bytes paths, debounce coalescing,
  callback-exception handling) plus a real-`Observer` integration test. The
  debounce interval is now injectable.
- [x] **`service.py` `run()` test** — `run()`, `_setup_signals`, `_handle_signal`,
  `_windows_wait`, and `_shutdown` are covered by mocking `signal.pause` /
  `time.sleep`.
- [x] **Windows CI matrix** — the test job now runs on `{ubuntu, windows}` ×
  Python {3.10, 3.11, 3.12}.
- [x] **Reactive re-renders on state change** — `service._on_state_changed`
  re-renders every button whose `states` map references the changed variable.
- [x] **`set_brightness` action** — `config.example.yaml` no longer fakes a
  brightness cycle; the dim/max buttons call `set_brightness` for real and drive
  a reactive `dimmed` indicator via `set_state`.
- [x] **Font configuration** — `device.font`, `button.font`, and per-state `font`
  (file path or system name), with a `(spec, size)` font cache.
- [x] **Multi-line label centering** — labels render via `multiline_textbbox` /
  `multiline_text` with `align="center"`, subtracting the bbox origin offset.
- [x] **`--list-decks` CLI flag** — `list_decks()` prints index / serial / type /
  key count and exits.
- [x] **Retry loop on startup** — `--retry N` polls every N seconds via
  `_open_deck_with_retry`.
- [x] **`on_hold` action** — per-button `on_hold` + `hold_seconds`, driven by
  `threading.Timer`; a fired hold suppresses that press's `on_release`.
- [x] **Icon pre-scaling cache** — `renderer._load_scaled_icon` caches by
  `(path, mtime, size)` and prunes superseded mtimes.
- [x] **Systemd unit file** — `streamdeck.service` user-service template shipped.

### Remaining / new ideas

- [ ] **State→brightness binding** — let a state variable map to real brightness
  levels so a single `toggle` can cycle actual brightness (deeper version of the
  brightness work; the example currently uses discrete `set_brightness` buttons).
- [ ] **`on_hold` auto-repeat** — optionally re-fire `on_hold` on an interval
  while the key stays held (e.g. volume ramp).
- [ ] **Global icon-cache bound** — the scaled-icon cache prunes stale mtimes per
  path but has no overall size cap; an LRU would bound memory for huge configs.
- [ ] **`mypy`/type-check CI job** — annotations are thorough; a `mypy` gate would
  keep them honest.
- [ ] **Wire up or drop `StateStore.merge`/`snapshot`** — `merge` is part of the
  public API but unused by the service.
