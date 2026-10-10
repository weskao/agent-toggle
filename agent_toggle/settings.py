"""Typed user settings in ~/.agent-toggle/config.json (the file `config.py` already uses).

Precedence: environment > file > default. The file holds JSON-typed values under flat keys
("update_check": false, "harness.codex": false); keys this module does not know (e.g.
`telegram_chat_id` written by older code) are preserved on every write. A corrupt file or an
invalid value never raises: it is ignored with one `agent-toggle: ignoring KEY=VALUE (...)`
warning on stderr per process.

Public API: get, all, set, reset, reset_all, enabled_harnesses, validate, source.
Unknown keys and invalid values raise ValueError from get/validate/set/reset.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import fs, harnesses

CHAT_ID_RE = re.compile(r"-?\d{1,20}|@[A-Za-z0-9_]{5,64}")
HARNESS_NAMES: tuple[str, ...] = tuple(harnesses.build(Path("/")))
_TRUE, _FALSE = ("1", "true", "yes", "on"), ("0", "false", "no", "off")


@dataclass(frozen=True)
class Spec:
    kind: str                       # "bool" | "choice" | "chat_id"
    default: Any = None
    choices: tuple[str, ...] = ()
    env: str | None = None


DEFAULTS: dict[str, Spec] = {
    "update_check": Spec("bool", True, env="AGENT_TOGGLE_UPDATE_CHECK"),
    "color": Spec("choice", "auto", ("auto", "always", "never"), "AGENT_TOGGLE_COLOR"),
    "default_harness": Spec("choice", "claude", HARNESS_NAMES, "AGENT_TOGGLE_DEFAULT_HARNESS"),
    **{f"harness.{n}": Spec("bool", True) for n in HARNESS_NAMES},
    "picker_sort": Spec("choice", "name", ("name", "cost")),
    "picker_harness": Spec("choice", "all", ("all", *HARNESS_NAMES)),
    "picker_type": Spec("choice", "all", ("all", *harnesses.TYPES, "mod")),
    "language": Spec("choice", "en", ("en", "zh-TW"), "AGENT_TOGGLE_LANG"),
    "telegram_chat_id": Spec("chat_id"),
}
_warned: dict[tuple[str, str], bool] = {}   # a dict: this module defines set()


def _spec(key: str) -> Spec:
    try:
        return DEFAULTS[key]
    except KeyError:
        raise ValueError(f"unknown setting {key!r}") from None


def validate(key: str, value: Any) -> Any:
    """The normalised value, or ValueError. Bool keys also take 0/1/true/false/yes/no/on/off text."""
    spec = _spec(key)
    if spec.kind == "bool":
        text = value.strip().lower() if isinstance(value, str) else value
        if isinstance(text, bool):
            return text
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        raise ValueError("expected a boolean (true/false)")
    if not isinstance(value, str):
        raise ValueError("expected a string")
    if spec.kind == "chat_id":
        if not CHAT_ID_RE.fullmatch(value):
            raise ValueError("expected a number such as -100123 or an @channel")
        return value
    for choice in spec.choices:
        if value.lower() == choice.lower():
            return choice
    raise ValueError("expected one of " + ", ".join(spec.choices))


def _warn(key: str, shown: str, why: str) -> None:
    if (key, shown) not in _warned:
        _warned[key, shown] = True
        if key == "telegram_chat_id":     # may be a pasted secret: never echo it
            print(f"agent-toggle: ignoring {key} (invalid value)", file=sys.stderr)
            return
        print(f"agent-toggle: ignoring {key}={shown} ({why})", file=sys.stderr)


def config_file() -> Path:
    return fs.state_dir() / "config.json"


def raw() -> dict:
    """The file as a dict; {} when missing, unreadable, corrupt or not an object."""
    try:
        data = json.loads(config_file().read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):     # RecursionError: "[" * 200000
        return {}
    return data if isinstance(data, dict) else {}


def _env(key: str) -> tuple[bool, Any]:
    spec = DEFAULTS[key]
    text = os.environ.get(spec.env, "") if spec.env else ""
    if not text:
        return False, None
    try:
        return True, validate(key, text)
    except ValueError as e:
        _warn(spec.env, text, str(e))
        return False, None


def _file(key: str, data: dict) -> tuple[bool, Any]:
    if key not in data:
        return False, None
    value = data[key]
    try:
        if DEFAULTS[key].kind == "bool" and not isinstance(value, bool):
            raise ValueError("expected a JSON boolean")
        return True, validate(key, value)
    except ValueError as e:
        _warn(key, json.dumps(value), str(e))
        return False, None


def _resolve(key: str, data: dict) -> tuple[Any, str]:
    for src, found in (("env", _env(key)), ("file", _file(key, data))):
        if found[0]:
            return found[1], src
    return _spec(key).default, "default"


def get(key: str) -> Any:
    _spec(key)
    return _resolve(key, raw())[0]


def source(key: str) -> str:
    """"env", "file" or "default": where get(key) comes from."""
    _spec(key)
    return _resolve(key, raw())[1]


def all() -> dict[str, Any]:
    data = raw()
    return {k: _resolve(k, data)[0] for k in DEFAULTS}


def enabled_harnesses() -> list[str]:
    data = raw()
    return [n for n in HARNESS_NAMES if _resolve(f"harness.{n}", data)[0]]


def _write(data: dict) -> None:
    fs.private_dir(fs.state_dir())
    fs.atomic_write(config_file(), json.dumps(data, indent=2) + "\n")


def set(key: str, value: Any) -> Any:  # noqa: A001 - the API name is part of the contract
    """Validate, then persist (other keys, known or not, are kept). Returns the stored value."""
    value = validate(key, value)
    data = raw()
    data[key] = value
    _write(data)
    return value


def reset(key: str) -> None:
    _spec(key)
    data = raw()
    if key in data:
        del data[key]
        _write(data)


def reset_all() -> None:
    """Drop every known key from the file; unknown keys stay."""
    data = raw()
    kept = {k: v for k, v in data.items() if k not in DEFAULTS}
    if kept != data:
        _write(kept)
