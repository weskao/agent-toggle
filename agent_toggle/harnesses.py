"""The harness table: which harnesses exist, where they live, what they support."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from . import fs
from .output import die

TYPES = ("skill", "agent", "command", "rule", "plugin", "mcp")

# The on-disk SHAPE (directory vs .md file) is probed, not declared:
# harnesses disagree and the probe is both shorter and more correct than a
# per-harness shape table.
PROBE_SUFFIXES = ("", ".md", ".toml", ".yaml", ".yml")


@dataclass(frozen=True)
class McpSpec:
    backend: str                 # "claude-json" | "toml"
    file: Path                   # the config file holding the servers
    key_path: tuple[str, ...]    # where the servers live inside it


@dataclass(frozen=True)
class Harness:
    name: str
    home: Path
    dirs: Mapping[str, tuple[str, ...]]      # type -> candidate subdirs
    mechanisms: Mapping[str, str]            # type -> move|flag|remove_backup|native_cli
    mcp: McpSpec | None = None
    flags: Mapping[str, tuple] = field(default_factory=dict)   # type -> (file, pointer)
    editable: frozenset[str] = frozenset()   # files the tool may write
    aliases_from: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # frozen only stops rebinding; wrap the dicts so they cannot be mutated either
        for f in ("dirs", "mechanisms", "flags"):
            object.__setattr__(self, f, MappingProxyType(dict(getattr(self, f))))

    @property
    def types(self) -> tuple[str, ...]:
        """Supported types, in the canonical TYPES order."""
        return tuple(t for t in TYPES if t in self.mechanisms)

    @property
    def backend(self) -> str | None:
        return self.mcp.backend if self.mcp else None


def build(home: Path) -> dict[str, Harness]:
    """The table for a given user home."""
    claude, codex, grok = home / ".claude", home / ".codex", home / ".grok"
    return {h.name: h for h in (
        Harness("claude", claude,
                dirs={"skill": ("skills",), "agent": ("agents",), "command": ("commands",),
                      "rule": ("rules",)},
                mechanisms={"skill": "move", "agent": "move", "command": "move",
                            "rule": "move", "plugin": "native_cli", "mcp": "remove_backup"},
                mcp=McpSpec("claude-json", home / ".claude.json", ("mcpServers",))),
        Harness("codex", codex,
                dirs={"skill": ("skills",), "agent": ("agents",),
                      "command": ("commands", "prompts")},
                mechanisms={"skill": "move", "agent": "move", "command": "move",
                            "plugin": "native_cli", "mcp": "remove_backup"},
                mcp=McpSpec("toml", codex / "config.toml", ("mcp_servers",))),
        # DESIGN §4 survey: grok keeps `[mcp_servers.<name>]` blocks in config.toml,
        # the same shape as codex, so the existing TOML backend serves it.
        Harness("grok", grok,
                dirs={"skill": ("skills",)},
                mechanisms={"skill": "move", "mcp": "remove_backup"},
                mcp=McpSpec("toml", grok / "config.toml", ("mcp_servers",))),
        Harness("openclaw", home / ".openclaw",
                dirs={"skill": ("skills",), "agent": ("agents",)},
                mechanisms={"skill": "move", "agent": "move"}),
    )}


def harnesses() -> dict[str, Harness]:
    """The table for the CURRENT home."""
    return build(fs.home())


def harness_of(name: str) -> Harness:
    table = harnesses()
    if name not in table:
        die(f"unknown harness {name!r} (expected: {', '.join(table)})")
    return table[name]
