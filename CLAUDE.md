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
  actions.py        dispatch() — pure function, no hardware reference
  watcher.py        Debounced (0.5 s) watchdog observer
  service.py        Ties everything together; owns the deck handle
  __main__.py       CLI entry point (--config, --log-level)
```

### Key decisions

- **Pydantic v2 discriminated union** for actions — `type: Literal[...]` on each
  action model means Pydantic auto-selects the right class; no manual parsing.
- **`AnyAction` type alias** (`CommandAction | SetStateAction | ...`) used as the
  field type for `on_press` / `on_release` lists.
- **State store listeners** fire synchronously after every `set()` call. Currently
  only used for debug logging, but wired up for future reactive re-renders.
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
  reload fires.
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

buttons:
  - key: int             # physical key index
    label: str | null
    font_size: int (14)
    text_color: str ("white")
    background_color: str ("black")
    icon: str | null     # path relative to config file
    states:
      <var_name>:
        <value>:          # merged over defaults when store[var_name] == value
          label / font_size / text_color / background_color / icon
    on_press: [Action]   # single dict also accepted (coerced to list)
    on_release: [Action]
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
  test_actions.py      dispatch(), Popen mocking, error paths
  test_renderer.py     pixel-level colour assertions, state overrides, icon load
  test_service.py      fresh render, config diff, key events, reload, open_deck
```

Run: `python -m pytest tests/ --cov=streamdeck_service --cov-report=term-missing`

Coverage: ~80% total. Uncovered lines are mostly the `run()` blocking loop,
signal handlers, `_windows_wait`, and `ConfigWatcher` (watchdog observer) — all
require live hardware or OS-level signals to exercise.

---

## CI

`.github/workflows/ci.yml` — two jobs on every push / PR against `main`:

| Job | Steps |
|-----|-------|
| `lint` | `ruff check` + `ruff format --check` |
| `test` | `pytest --cov --cov-fail-under=80` on Python 3.10, 3.11, 3.12 |

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

### High priority

- [ ] **`watcher.py` test coverage (currently 37%)** — the `ConfigWatcher` /
  `_ConfigHandler` classes need integration tests using `tmp_path` + a real
  `Observer` or a mock observer to exercise debounce logic.
- [ ] **`service.py` `run()` test** — the blocking entry point (`signal.pause` /
  `_windows_wait`), signal handler, and `_shutdown` are untested. Could be
  exercised with `threading.Thread` + `os.kill` in a test.
- [ ] **Windows CI matrix** — add `runs-on: windows-latest` to the test job;
  `signal.pause` path is currently only tested on Linux.

### Medium priority

- [ ] **Reactive re-renders on state change** — `StateStore` already fires
  listeners; wire a listener in `service.py` that re-renders every key whose
  `states` map references the changed variable (currently only re-renders the
  pressed key).
- [ ] **`set_brightness` action in toggle** — the brightness-cycle button in
  `config.example.yaml` uses named levels but doesn't actually call
  `set_brightness`; a follow-up could add a `script` action or let `toggle`
  drive a `set_brightness` side-effect.
- [ ] **Font path configuration** — `renderer.py` hard-codes `DejaVuSans-Bold.ttf`
  then `arial.ttf` then Pillow default. A `font:` key in `device:` or per-button
  would be cleaner.
- [ ] **Multi-line label centering** — `\n` in labels renders but the vertical
  centering uses only `textbbox` of the full string, which may be off for tall
  fonts. Use `ImageDraw.multiline_textbbox` instead.

### Low priority / nice-to-have

- [ ] **`--list-decks` CLI flag** — enumerate connected decks and print serial /
  index / type, useful for writing the `device.serial` config value.
- [ ] **Retry loop on startup** — currently exits if no deck found; a `--retry`
  flag that polls every N seconds would be friendlier for service managers
  (systemd, launchd).
- [ ] **`on_hold` action** — trigger after a configurable press duration.
- [ ] **Icon pre-scaling cache** — icons are resized on every render; cache the
  scaled `Image` keyed by `(path, mtime, size)` to avoid repeated disk I/O.
- [ ] **Systemd unit file** — ship a `streamdeck.service` template for Linux
  auto-start.
