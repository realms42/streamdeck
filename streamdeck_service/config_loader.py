"""Load and validate YAML config files into StreamDeckConfig models."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import ValidationError

from .models import StreamDeckConfig

log = logging.getLogger(__name__)


def load_config(path: Path) -> StreamDeckConfig | None:
    """Parse *path* and return a validated StreamDeckConfig, or None on error.

    Errors are logged; callers must decide whether to abort or keep the last
    good config.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        log.error("Config file not found: %s", path)
        return None
    except yaml.YAMLError as exc:
        log.error("YAML parse error in %s: %s", path, exc)
        return None

    if raw is None:
        log.error("Config file is empty: %s", path)
        return None

    try:
        cfg = StreamDeckConfig.model_validate(raw)
    except ValidationError as exc:
        log.error("Config validation failed for %s:\n%s", path, _fmt_validation_error(exc))
        return None

    # Validate that referenced icon paths exist
    errors = _check_icon_paths(cfg, path.parent)
    if errors:
        for e in errors:
            log.error("Config error: %s", e)
        return None

    log.info("Config loaded successfully from %s (%d button(s))", path, len(cfg.buttons))
    return cfg


def _fmt_validation_error(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        loc = " -> ".join(str(part) for part in err["loc"])
        lines.append(f"  [{loc}] {err['msg']}")
    return "\n".join(lines)


def _check_icon_paths(cfg: StreamDeckConfig, base_dir: Path) -> list[str]:
    errors: list[str] = []
    for btn in cfg.buttons:
        if btn.icon and not (base_dir / btn.icon).exists():
            errors.append(f"Button {btn.key}: icon not found: {btn.icon}")
        for var_name, mapping in btn.states.items():
            for state_val, appearance in mapping.items():
                if appearance.icon and not (base_dir / appearance.icon).exists():
                    errors.append(
                        f"Button {btn.key} state {var_name}={state_val}: "
                        f"icon not found: {appearance.icon}"
                    )
    return errors
