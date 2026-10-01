"""The harness table: which harnesses exist, where they live, what they support."""
from __future__ import annotations

from pathlib import Path

from . import fs
from .output import die

TYPES = ("skill", "agent", "command", "plugin", "mcp")

# type -> subdirectory name. The on-disk SHAPE (directory vs .md file) is
# probed, not declared: harnesses disagree and the probe is both shorter and
# more correct than a per-harness shape table.
SUBDIRS = {"skill": "skills", "agent": "agents", "command": "commands"}
PROBE_SUFFIXES = ("", ".md", ".toml", ".yaml", ".yml")


def harnesses() -> dict[str, tuple[Path, tuple[str, ...], str | None]]:
    """harness -> (home dir, supported types, mcp backend), for the CURRENT home."""
    home = fs.home()
    return {
        "claude":   (home / ".claude",   ("skill", "agent", "command", "plugin", "mcp"), "claude-json"),
        "codex":    (home / ".codex",    ("skill", "agent", "command", "plugin", "mcp"), "toml"),
        "grok":     (home / ".grok",     ("skill",), None),
        "openclaw": (home / ".openclaw", ("skill", "agent"), None),
    }


def harness_of(name: str) -> tuple[Path, tuple[str, ...], str | None]:
    table = harnesses()
    if name not in table:
        die(f"unknown harness {name!r} (expected: {', '.join(table)})")
    return table[name]
