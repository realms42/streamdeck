"""Pydantic config models — the single source of truth for YAML schema."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


class CommandAction(BaseModel):
    type: Literal["command"]
    run: str
    args: list[str] = Field(default_factory=list)


class SetStateAction(BaseModel):
    type: Literal["set_state"]
    key: str  # state variable name
    value: Any


class ToggleAction(BaseModel):
    type: Literal["toggle"]
    key: str  # state variable name
    values: list[Any] = Field(..., min_length=2, max_length=2)


class SetBrightnessAction(BaseModel):
    type: Literal["set_brightness"]
    value: int = Field(..., ge=0, le=100)


AnyAction = CommandAction | SetStateAction | ToggleAction | SetBrightnessAction


# ---------------------------------------------------------------------------
# Button appearance
# ---------------------------------------------------------------------------


class StateAppearance(BaseModel):
    """Visual overrides applied when a named state variable has a specific value."""

    label: str | None = None
    font_size: int | None = Field(None, ge=4, le=200)
    text_color: str | None = None
    background_color: str | None = None
    icon: str | None = None


class ButtonDef(BaseModel):
    key: int = Field(..., ge=0)

    # --- appearance defaults ---
    label: str | None = None
    font_size: int = Field(default=14, ge=4, le=200)
    text_color: str = "white"
    background_color: str = "black"
    icon: str | None = None

    # state_var -> {state_value -> StateAppearance}
    # e.g.  states: {muted: {true: {icon: muted.png, label: "Muted"}}}
    states: dict[str, dict[str, StateAppearance]] = Field(default_factory=dict)

    # --- behavior ---
    on_press: list[AnyAction] = Field(default_factory=list)
    on_release: list[AnyAction] = Field(default_factory=list)

    @field_validator("on_press", "on_release", mode="before")
    @classmethod
    def _coerce_single(cls, v: Any) -> Any:
        """Allow a single action dict instead of a list."""
        if isinstance(v, dict):
            return [v]
        return v

    @model_validator(mode="after")
    def _validate_states(self) -> ButtonDef:
        for _var_name, mapping in self.states.items():
            for _state_val, appearance in mapping.items():
                if appearance.icon is not None:
                    # icon path validation happens later when the file is loaded
                    pass
        return self


# ---------------------------------------------------------------------------
# Device selector
# ---------------------------------------------------------------------------


class DeviceConfig(BaseModel):
    serial: str | None = None  # preferred selector
    index: int = Field(default=0, ge=0)  # fallback
    brightness: int = Field(default=70, ge=0, le=100)


# ---------------------------------------------------------------------------
# Root config
# ---------------------------------------------------------------------------


class StreamDeckConfig(BaseModel):
    device: DeviceConfig = Field(default_factory=DeviceConfig)
    buttons: list[ButtonDef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_keys(self) -> StreamDeckConfig:
        seen: set[int] = set()
        for btn in self.buttons:
            if btn.key in seen:
                raise ValueError(f"Duplicate button key: {btn.key}")
            seen.add(btn.key)
        return self

    def button_by_key(self, key: int) -> ButtonDef | None:
        for btn in self.buttons:
            if btn.key == key:
                return btn
        return None
