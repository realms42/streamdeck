"""Action dispatcher — execute button actions against the state store and deck."""

from __future__ import annotations

import logging
import shlex
import subprocess
from typing import TYPE_CHECKING, Callable

from .models import AnyAction, CommandAction, SetBrightnessAction, SetStateAction, ToggleAction
from .state_store import StateStore

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


def dispatch(
    action: AnyAction,
    state: StateStore,
    set_brightness: Callable[[int], None],
) -> None:
    """Execute a single action.

    *set_brightness* is a callable injected by the service so this module
    doesn't need a direct reference to the deck hardware.
    """
    if isinstance(action, CommandAction):
        _run_command(action)
    elif isinstance(action, SetStateAction):
        state.set(action.key, action.value)
    elif isinstance(action, ToggleAction):
        state.toggle(action.key, action.values)
    elif isinstance(action, SetBrightnessAction):
        set_brightness(action.value)
    else:
        log.warning("Unknown action type: %s", type(action))


def _run_command(action: CommandAction) -> None:
    cmd_parts = shlex.split(action.run) + list(action.args)
    log.info("Running command: %s", cmd_parts)
    try:
        subprocess.Popen(
            cmd_parts,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            # Detach so the service doesn't wait for it
            start_new_session=True,
        )
    except FileNotFoundError:
        log.error("Command not found: %s", cmd_parts[0])
    except OSError as exc:
        log.error("Failed to launch command %s: %s", cmd_parts, exc)
