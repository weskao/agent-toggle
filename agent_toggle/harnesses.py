"""The harness table: which harnesses exist, where they live, what they support."""
from __future__ import annotations

import json
import os
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
    backend: str                 # "claude-json" | "toml" | "json" | "project-json"
    file: Path                   # the config file holding the servers
    key_path: tuple[str, ...]    # where the servers live inside it


@dataclass(frozen=True)
class Harness:
    name: str
    home: Path
    dirs: Mapping[str, tuple[str, ...]]      # type -> candidate subdirs
    mechanisms: Mapping[str, str]            # type -> move|flag|remove_backup|native_cli
    mcp: McpSpec | None = None
    # type -> (file relative to home, pointer tuple; "<name>" is replaced by the item name)
    flags: Mapping[str, tuple] = field(default_factory=dict)
    editable: frozenset[str] = frozenset()   # files the tool may write
    aliases_from: tuple[str, ...] = ()
    project: Path | None = None              # set only on a --project view (resolved dir)

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


def opencode_home(home: Path) -> Path:
    """`$XDG_CONFIG_HOME/opencode`, else `<home>/.config/opencode` (read at call time).

    A relative XDG_CONFIG_HOME is ignored, per the XDG spec.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    return (Path(xdg) if os.path.isabs(xdg) else home / ".config") / "opencode"


def opencode_skill_dirs(home: Path, oc: Path) -> tuple[str, ...]:
    """OpenCode's skill dirs: its own `skills/` plus every `opencode.json` ->
    `skills.paths[]` entry (the redirect DESIGN s4 pins; on the surveyed machine
    it points at codex's skills dir). A path is `~/`-expanded against `home`, a
    relative one is taken relative to `oc` (assumption: DESIGN does not say).
    Extra dirs are absolute strings, which `home / sub` passes through intact.
    A missing or malformed file adds nothing: the table build never fails.
    """
    try:
        paths = json.loads((oc / "opencode.json").read_text(encoding="utf-8"))["skills"]["paths"]
    except (OSError, ValueError, KeyError, TypeError):
        paths = []
    extra = []
    for p in paths if isinstance(paths, list) else []:
        if isinstance(p, str) and p:
            q = home / p[2:] if p.startswith("~/") else oc / p
            extra.append(str(q))
    return tuple(dict.fromkeys(("skills", *extra)))


def build(home: Path) -> dict[str, Harness]:
    """The table for a given user home."""
    claude, codex, grok = home / ".claude", home / ".codex", home / ".grok"
    oc = opencode_home(home)
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
        # DESIGN s4: command/*.md; skills may be redirected by opencode.json.
        # Assumption (DESIGN s11 q2): with no skills.paths, `skills/` is its own dir.
        Harness("opencode", oc,
                dirs={"skill": opencode_skill_dirs(home, oc), "command": ("command",)},
                # ASSUMED shape (DESIGN s4/s11, not verified on a real install):
                # `mcp.<name>.enabled` in opencode.json, strict JSON only.
                mechanisms={"skill": "move", "command": "move", "mcp": "flag"},
                flags={"mcp": ("opencode.json", ("mcp", "<name>", "enabled"))},
                editable=frozenset({"opencode.json"}),
                aliases_from=("skills.paths",)),
        # ASSUMED shapes (DESIGN s4/s11, not verified on a real install):
        # `skills.entries.<name>.enabled` and `plugins.entries.<name>.enabled` in
        # openclaw.json. A skill uses the flag only when its entry exists, else
        # the dir move; a plugin is flag-only.
        Harness("openclaw", home / ".openclaw",
                dirs={"skill": ("skills",), "agent": ("agents",)},
                mechanisms={"skill": "move", "agent": "move", "plugin": "flag"},
                flags={"skill": ("openclaw.json", ("skills", "entries", "<name>", "enabled")),
                       "plugin": ("openclaw.json", ("plugins", "entries", "<name>", "enabled"))},
                editable=frozenset({"openclaw.json"})),
        # DESIGN s4: `mcp-config.json -> mcpServers`, strict JSON, backend "json". Its
        # `config.json` is machine-managed JSONC: never listed, never in `editable`.
        Harness("copilot", home / ".copilot",
                dirs={"skill": ("skills",), "agent": ("agents",)},
                mechanisms={"skill": "move", "agent": "move", "mcp": "remove_backup"},
                mcp=McpSpec("json", home / ".copilot" / "mcp-config.json", ("mcpServers",)),
                editable=frozenset({"mcp-config.json"})),
        # DESIGN s4: vibe keeps skills/<name>/SKILL.md; no MCP config found.
        Harness("vibe", home / ".vibe",
                dirs={"skill": ("skills",)},
                mechanisms={"skill": "move"}),
        # DESIGN s4: nothing togglable locally (resources are cloud-side). An
        # explicit not-applicable row: no dirs, no mechanisms, so every type
        # exits 4 through the ops dispatch.
        Harness("devin", home / ".devin", dirs={}, mechanisms={}),
        # DESIGN s11 q1: skills layout unobserved, so types=() and NO adapter yet
        # (`status` lists it as "found, nothing supported yet").
        Harness("agy", home / ".antigravity", dirs={}, mechanisms={}),
    )}


def harnesses() -> dict[str, Harness]:
    """The table for the CURRENT home."""
    return build(fs.home())


PROJECT_TYPES = ("skill", "agent", "command", "rule")


def project_view(project: Path | str) -> Harness:
    """The claude-shaped row for `--project <dir>` (DESIGN s5.5): home=<dir>/.claude,
    the dir types plus `mcp` (the repo's own <dir>/.mcp.json, strict JSON, edited
    directly). Refuses (CliError) a dir that is missing (4), has neither .claude
    nor .mcp.json (4),
    is a root / $HOME / an ancestor of $HOME / inside our state dir or a user harness
    home, or whose .claude resolves outside it (2):
    a project view must never address user-scope files."""
    p = Path(project)
    if not p.is_dir():
        die(f"--project {project}: no such directory", 4)
    p = p.resolve()
    if p == Path(p.anchor) or fs.home().resolve().is_relative_to(p):
        die(f"--project {p} is a root, $HOME or holds $HOME -- drop --project for user scope", 2)
    for root in (fs.state_dir(), *(h.home for h in harnesses().values())):
        if p.is_relative_to(root.resolve()):     # user-scope files under a project key
            die(f"--project {p} is inside {root} -- not a project", 2)
    claude = p / ".claude"
    if not claude.is_dir() and not (p / ".mcp.json").is_file():
        die(f"--project {p} has no .claude directory or .mcp.json", 4)
    if os.path.lexists(claude) and not claude.resolve().is_relative_to(p):
        die(f"--project {p}: .claude resolves outside the project ({claude.resolve()})", 2)
    return Harness("claude", claude,
                   dirs={"skill": ("skills",), "agent": ("agents",), "command": ("commands",),
                         "rule": ("rules",)},
                   mechanisms={**dict.fromkeys(PROJECT_TYPES, "move"), "mcp": "remove_backup"},
                   mcp=McpSpec("project-json", p / ".mcp.json", ("mcpServers",)), project=p)


def harness_of(name: str) -> Harness:
    table = harnesses()
    if name not in table:
        die(f"unknown harness {name!r} (expected: {', '.join(table)})")
    return table[name]
