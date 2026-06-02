# Stream Deck Service

A Python service that drives an Elgato Stream Deck from a YAML config file, with **live reload** when the config changes on disk.

## Features

- Declarative YAML config — buttons, icons, labels, colors, fonts, actions
- Named state variables — buttons change appearance based on runtime state (toggle, set_state)
- Reactive re-rendering — when a state variable changes, every button that depends on it updates
- Action types: `command`, `set_state`, `toggle`, `set_brightness`
- Press, release, and long-press (`on_hold`) action hooks
- Configurable fonts (per device, per button, or per state) with multi-line, centered labels
- Live reload with debouncing — edits in any text editor reload only changed keys
- Invalid configs are rejected with clear error messages; the deck keeps running on the last good config
- Clean shutdown on Ctrl-C / SIGTERM
- Works on Linux and Windows with identical application code

---

## Requirements

- Python 3.10+
- An Elgato Stream Deck (Original, Mini, XL, MK.2, +, Neo, Pedal, …)

---

## Installation

### Linux

**1. Install system hidapi**

```bash
# Debian / Ubuntu
sudo apt install libhidapi-hidraw0 libhidapi-libusb0

# Fedora / RHEL
sudo dnf install hidapi

# Arch
sudo pacman -S hidapi
```

**2. Install the udev rule** (grants unprivileged USB access)

```bash
sudo cp 70-streamdeck.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
sudo usermod -aG plugdev $USER
```

Log out and back in (or run `newgrp plugdev`) for the group change to take effect.

**3. Install Python dependencies**

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# or: pip install .
```

---

### Windows

**1. Install hidapi**

The `streamdeck` library bundles the hidapi DLL on Windows via the `hidapi` Python package.  No separate system install is needed.

```powershell
pip install hidapi
```

If you see a DLL load error, install the [Visual C++ Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe).

**2. Install Python dependencies**

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

---

## Running

```bash
# Default: looks for config.yaml in the current directory
python -m streamdeck_service

# Custom config path
python -m streamdeck_service --config /path/to/my_config.yaml

# Verbose logging
python -m streamdeck_service --log-level DEBUG

# List connected decks (index, serial, type, key count) and exit —
# useful for filling in device.serial
python -m streamdeck_service --list-decks

# Wait for a deck instead of exiting if none is connected at startup
# (polls every 5 seconds)
python -m streamdeck_service --retry 5
```

Or, if installed as a package:

```bash
streamdeck-service --config config.yaml
```

---

## Config format

See [`config.example.yaml`](config.example.yaml) for a fully annotated example.

### Top-level structure

```yaml
device:
  serial: "CL12345678"   # optional; omit to use index
  index: 0               # 0-based device index (default 0)
  brightness: 75         # 0–100 (default 70)
  font: "DejaVuSans-Bold.ttf"  # optional default font for all buttons

buttons:
  - key: 0
    ...
```

### Button appearance

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `key` | int | — | Physical key index (0-based, required) |
| `label` | string | `null` | Text drawn on the key (`\n` for multiple, centered lines) |
| `font_size` | int | `14` | Font size in pixels |
| `text_color` | string | `"white"` | CSS color or hex (`#rrggbb`) |
| `background_color` | string | `"black"` | CSS color or hex |
| `icon` | string | `null` | Path to PNG/JPEG relative to the config file |
| `font` | string | `null` | Font file (relative to config) or system font name; overrides `device.font` |
| `hold_seconds` | float | `0.6` | How long a press must last before `on_hold` fires |

### State-driven appearance (`states`)

```yaml
states:
  <state_variable_name>:
    <state_value>:
      label: "..."          # any appearance field may be overridden,
      background_color: "#cc0000"   # including font / font_size / text_color
      icon: icons/off.png
```

When a state variable changes (via `set_state` or `toggle`), **every** button whose
`states` map references that variable is re-rendered automatically.

### Actions (`on_press` / `on_release` / `on_hold`)

Each is a list of actions executed in order (a single action may be written as a
bare mapping instead of a one-item list).

- `on_press` — fires immediately on key-down.
- `on_release` — fires on key-up.
- `on_hold` — fires once if the key is still held after `hold_seconds`. When a
  hold fires, the matching `on_release` for that press is skipped.

#### `command`
```yaml
- type: command
  run: "xdg-open"        # executable; shell splitting applied to `run`
  args: ["https://..."]  # optional extra args
```

#### `set_state`
```yaml
- type: set_state
  key: my_var
  value: "some_value"
```

#### `toggle`
```yaml
- type: toggle
  key: muted
  values: ["false", "true"]   # cycles through these values
```

#### `set_brightness`
```yaml
- type: set_brightness
  value: 80    # 0–100
```

---

## Live reload

Edit and save the config file — changes are applied within ~0.5 s.  Only keys whose definition changed are re-rendered.  Runtime state (e.g. which toggle is active) is preserved across reloads.  If the new config has a syntax error or references a missing file, an error is logged and the deck continues running on the previous config.

---

## Auto-start on Linux (systemd)

A `streamdeck.service` template is included.  Install it as a **user** service so
it keeps the unprivileged USB access from the udev rule and can reach your desktop
session:

```bash
mkdir -p ~/.config/systemd/user ~/.config/streamdeck
cp config.example.yaml ~/.config/streamdeck/config.yaml   # then edit it
cp streamdeck.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now streamdeck.service
journalctl --user -u streamdeck.service -f   # follow the logs
```

The unit starts the service with `--retry 5` so it waits for the deck to be
plugged in rather than exiting.

---

## Project layout

```
streamdeck_service/
  __init__.py       — package marker
  __main__.py       — CLI entry point (--config, --log-level)
  models.py         — Pydantic config schema (StreamDeckConfig, ButtonDef, actions…)
  config_loader.py  — YAML parse + Pydantic validation + icon-path checks
  renderer.py       — Pillow-based key image renderer (text, icon, state overrides)
  state_store.py    — Thread-safe runtime state dict with change listeners
  actions.py        — Action dispatcher (command, set_state, toggle, set_brightness)
  watcher.py        — Debounced watchdog file watcher
  service.py        — Main service: device lifecycle, config diff, key callbacks
config.example.yaml — Annotated example demonstrating every feature
70-streamdeck.rules — Linux udev rule for unprivileged USB access
streamdeck.service  — systemd user-service template for auto-start
requirements.txt
pyproject.toml
```

---

## Troubleshooting

### `No Stream Deck found`
- **Linux:** ensure the udev rule is installed and you are in the `plugdev` group (log out and back in after `usermod`).
- **Windows:** try running as Administrator the first time; the HID driver should then work for your user.

### Permission error on `/dev/hidraw*`
Re-run the udev steps and verify with `ls -l /dev/hidraw*`.

### Icon not rendering
Paths in `icon:` are relative to the directory containing the config file.  Check the path and ensure the file exists.

### Deck goes blank after a bad edit
The service caught a validation error and kept the last good config.  Check stderr for the error message.
