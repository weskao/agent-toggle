"""The harness table: which harnesses exist, where they live, what they support."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from . import fs
from .backends import flag_json
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
    project: Path | None = None              # set only on a --project view (resolved dir)
    # harness version this row's layout was last checked against (docs/harnesses.md);
    # "unverified" = no version found, or nothing in the row to check
    verified: str = "unverified"

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


def _expand_home(p: str, home: Path) -> str | None:
    """`~`, `~/x`, `$HOME/x`, `${HOME}/x` against `home` (not the process env, so a
    sandboxed home is honoured); an absolute path as is; anything else (a relative
    path, `~user`, other `$VARS`) -> None."""
    for pre in ("~", "$HOME", "${HOME}"):
        if p == pre or p.startswith(pre + "/"):
            return str(home / p[len(pre) + 1:])
    return p if os.path.isabs(p) else None


def opencode_skill_dirs(home: Path, oc: Path) -> tuple[str, ...]:
    """Every dir OpenCode (2.0.22, DESIGN s11 q2) scans for skills, configured
    redirects first (so `dirs["skill"][0]` is where `install-shims` writes):

    1. `skills.paths[]` from `opencode.json`, else `opencode.jsonc` (the first that
       exists wins; JSONC allowed; `skills` may be `{"paths": [...]}` or a bare
       list). `~`, `$HOME/`, `${HOME}/` expand against `home`. A relative entry
       resolves against the session cwd, which this home-based table does not
       have, so it is skipped (a `--project` view is claude-shaped only).
    2. `<oc>/skills` and `<oc>/skill`: `paths` is additive, they stay scanned.
    3. `~/.claude/skills` and `~/.agents/skills`, but only when OpenCode is
       installed (`oc` exists): they are other harnesses' dirs, so `dir_view`
       treats them as one shared dir (parked once under its owner).

    Extra dirs are absolute strings, which `home / sub` passes through intact.
    A missing or malformed file adds nothing: the table build never fails.
    """
    paths: object = []
    for name in ("opencode.json", "opencode.jsonc"):
        try:
            text = (oc / name).read_text(encoding="utf-8")
        except OSError:
            continue
        except ValueError:          # not UTF-8: unreadable, never fatal
            break
        try:
            skills = flag_json.jsonc_loads(text)["skills"]
            paths = skills.get("paths") if isinstance(skills, dict) else skills
        except (ValueError, KeyError, TypeError):
            pass
        break
    extra = [q for p in paths if isinstance(p, str) and p
             and (q := _expand_home(p, home)) is not None] if isinstance(paths, list) else []
    compat = [str(home / ".claude" / "skills"), str(home / ".agents" / "skills")] if oc.is_dir() else []
    return tuple(dict.fromkeys((*extra, "skills", "skill", *compat)))


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
                mcp=McpSpec("claude-json", home / ".claude.json", ("mcpServers",)),
                verified="2.1.288"),
        Harness("codex", codex,
                dirs={"skill": ("skills",), "agent": ("agents",),
                      "command": ("commands", "prompts")},
                mechanisms={"skill": "move", "agent": "move", "command": "move",
                            "plugin": "native_cli", "mcp": "remove_backup"},
                mcp=McpSpec("toml", codex / "config.toml", ("mcp_servers",)),
                verified="0.160.0"),
        # DESIGN §4 survey: grok keeps `[mcp_servers.<name>]` blocks in config.toml,
        # the same shape as codex, so the existing TOML backend serves it.
        Harness("grok", grok,
                dirs={"skill": ("skills",)},
                mechanisms={"skill": "move", "mcp": "remove_backup"},
                mcp=McpSpec("toml", grok / "config.toml", ("mcp_servers",)),
                verified="1.0.44"),
        # DESIGN s4: command/*.md; skills may be redirected by opencode.json.
        # Skill dirs: see opencode_skill_dirs (DESIGN s11 q2, answered on 2.0.22).
        Harness("opencode", oc,
                dirs={"skill": opencode_skill_dirs(home, oc), "command": ("command",)},
                # ASSUMED shape (DESIGN s4/s11, not verified on a real install):
                # `mcp.<name>.enabled` in opencode.json, strict JSON only.
                mechanisms={"skill": "move", "command": "move", "mcp": "flag"},
                flags={"mcp": ("opencode.json", ("mcp", "<name>", "enabled"))},
                editable=frozenset({"opencode.json"}),
                verified="2.0.22"),
        # ASSUMED shapes (DESIGN s4/s11, not verified on a real install):
        # `skills.entries.<name>.enabled` and `plugins.entries.<name>.enabled` in
        # openclaw.json. A skill uses the flag only when its entry exists, else
        # the dir move; a plugin is flag-only.
        Harness("openclaw", home / ".openclaw",
                dirs={"skill": ("skills",), "agent": ("agents",)},
                mechanisms={"skill": "move", "agent": "move", "plugin": "flag"},
                flags={"skill": ("openclaw.json", ("skills", "entries", "<name>", "enabled")),
                       "plugin": ("openclaw.json", ("plugins", "entries", "<name>", "enabled"))},
                editable=frozenset({"openclaw.json"}), verified="2026.9.2"),
        # DESIGN s4: `mcp-config.json -> mcpServers`, strict JSON, backend "json". Its
        # `config.json` is machine-managed JSONC: never listed, never in `editable`.
        Harness("copilot", home / ".copilot",
                dirs={"skill": ("skills",), "agent": ("agents",)},
                mechanisms={"skill": "move", "agent": "move", "mcp": "remove_backup"},
                mcp=McpSpec("json", home / ".copilot" / "mcp-config.json", ("mcpServers",)),
                editable=frozenset({"mcp-config.json"}), verified="1.0.90"),
        # DESIGN s4: vibe keeps skills/<name>/SKILL.md; no MCP config found.
        Harness("vibe", home / ".vibe",
                dirs={"skill": ("skills",)},
                mechanisms={"skill": "move"}, verified="2.25.8"),
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
