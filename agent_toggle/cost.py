"""Startup token estimates for every togglable item (read-only, stdlib only).

Formula (documented +-25 %; `cost --json` carries it as `formula`):

    tokens = ceil(chars / 4)                      -- the tokenizer-free rule of thumb

    skill    chars of frontmatter `name` + `description` (body loads on invocation)
    agent    chars of frontmatter `name` + `description` + `tools`
    command  chars of the name + frontmatter `description` + `argument-hint`
    rule     chars of the WHOLE file (rules are injected in full)
    mcp      tools x per-tool weight; the tool count comes from the server entry's
             tool list / `[mcp_servers.X.tools.*]` tables when present, else a flat
             default. Harnesses that defer schemas (claude) load tool NAMES only:
             10 tok per tool, flat 20 tok per server when the count is unknown.
             Others: 300 tok per tool, flat 5 tools (1500 tok) when unknown.
    plugin   sum of its bundled skills/agents/commands/.mcp.json under `installPath`

A missing `name` falls back to the file/dir name. Frontmatter is read once, only the
head of the file up to the closing `---` (HEAD_CAP bytes at most); there is no cache.
Only the static, always-loaded text is estimated -- measured numbers come from the
harness. A parked item costs 0 now and reports `would_save`, the tokens it would load
if restored.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import fs
from .backends.flag_json import jsonc_loads
from .backends.mcp_json import ProjectMcpError, read_project_mcp
from .backends.plugin_cli import claude_bin, run_cli
from .mechanisms import _valid_name, dir_view, live_mcp, live_names, resolve_item

CHARS_PER_TOKEN = 4
HEAD_CAP = 16 * 1024          # bytes read per frontmatter block
RULE_CAP = 1024 * 1024        # bytes read per whole-file rule
MCP_TOKENS_PER_TOOL = {"full": 300, "deferred": 10}
MCP_FLAT = {"full": 5 * 300, "deferred": 20}      # per server, tool count unknown
DEFERS_SCHEMAS = frozenset({"claude"})            # loads MCP schemas on demand
FIELDS = {"skill": ("name", "description"),
          "agent": ("name", "description", "tools"),
          "command": ("description", "argument-hint")}

FORMULA = {
    "tokens": "ceil(chars / 4)", "accuracy": "+-25%", "chars_per_token": CHARS_PER_TOKEN,
    "counted": {**{t: "+".join(f) for t, f in FIELDS.items()}, "rule": "whole file",
                "plugin": "sum of bundled skills, agents, commands, mcp servers"},
    "mcp_tokens_per_tool": MCP_TOKENS_PER_TOOL, "mcp_flat_per_server": MCP_FLAT,
    "mcp_deferring_harnesses": sorted(DEFERS_SCHEMAS),
}
TOOL_SECTION = re.compile(r"^\[mcp_servers\.([^.\]\s]+)\.tools\.[^\]]+\]", re.M)

Estimate = tuple[int, "int | None", str]          # tokens, chars, basis


@dataclass
class Item:
    harness: str                  # the owning harness for a shared dir
    type: str
    name: str
    enabled: bool
    tokens: int                   # cost now: 0 when parked
    would_save: int               # parked: what restoring it would load
    chars: int | None
    basis: str
    shared_with: tuple[str, ...] = ()


def tokens_of(chars: int) -> int:
    return -(-chars // CHARS_PER_TOKEN)


def read_head(path: Path, cap: int) -> str:
    with open(path, "rb") as f:
        return f.read(cap).decode("utf-8", "replace")


def parse_frontmatter(head: str) -> dict[str, str]:
    """Top-level `key: value` pairs of a leading `---` block; indented lines continue
    the previous key (folded / literal scalars). Not a YAML parser."""
    lines = head.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict[str, str] = {}
    key = None
    for ln in lines[1:]:
        if ln.strip() == "---":
            break
        if ln[:1] in (" ", "\t") and key:
            out[key] = f"{out[key]} {ln.strip()}".strip()
        elif ":" in ln and not ln.lstrip().startswith("#"):
            k, _, v = ln.partition(":")
            key, v = k.strip(), v.strip()
            out[key] = "" if v in (">", "|", ">-", "|-") else v.strip("\"'")
    return out


def file_estimate(type_: str, name: str, path: Path | None) -> Estimate:
    """skill / agent / command / rule from one file (a skill dir -> its SKILL.md)."""
    if path is None:
        return 0, None, "file not found"
    if type_ == "skill" and path.is_dir():
        path = path / "SKILL.md"
    try:
        if type_ == "rule":
            chars = len(read_head(path, RULE_CAP))
            return tokens_of(chars), chars, f"whole file {chars} chars"
        fm = parse_frontmatter(read_head(path, HEAD_CAP))
    except OSError as e:
        return 0, None, f"unreadable: {e.strerror or e}"
    vals = {f: fm.get(f, "") for f in FIELDS[type_]}
    if not vals.get("name"):
        vals["name"] = name
    chars = sum(map(len, vals.values()))
    parts = "+".join(f for f, v in vals.items() if v)
    return tokens_of(chars), chars, f"{parts} {chars} chars" + ("" if fm else " (no frontmatter)")


def mcp_estimate(harness: str, tools: int | None) -> Estimate:
    mode = "deferred" if harness in DEFERS_SCHEMAS else "full"
    note = " (names only, deferred)" if mode == "deferred" else ""
    if not tools:
        return MCP_FLAT[mode], None, f"tool count unknown: {MCP_FLAT[mode]} tok flat{note}"
    per = MCP_TOKENS_PER_TOOL[mode]
    return tools * per, None, f"{tools} tools x {per}{note}"


def _entry_tools(entry) -> int | None:
    tools = entry.get("tools") if isinstance(entry, dict) else None
    if tools == ["*"]:                  # "all tools": a wildcard, not a count (flat estimate)
        return None
    return len(tools) if isinstance(tools, (list, dict)) and tools else None


def mcp_tool_counts(h) -> dict[str, int]:
    """name -> tool count for the live servers whose config lists their tools."""
    if h.backend == "toml":
        try:
            return dict(Counter(TOOL_SECTION.findall((h.home / "config.toml").read_text(encoding="utf-8"))))
        except OSError:
            return {}
    if h.backend == "claude-json":
        try:
            cfg = json.loads(fs.claude_json().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        servers = dict(cfg.get("mcpServers") or {})
        for pdata in (cfg.get("projects") or {}).values():
            if isinstance(pdata, dict):
                for n, e in (pdata.get("mcpServers") or {}).items():
                    servers.setdefault(n, e)
        return {n: c for n, e in servers.items() if (c := _entry_tools(e))}
    if h.backend == "json":
        try:
            servers = read_project_mcp(h.mcp.file)[1]["mcpServers"]
        except ProjectMcpError:
            return {}
        return {n: c for n, e in servers.items() if (c := _entry_tools(e))}
    return {}


def flag_names(h, type_: str) -> list[str]:
    """Names of the live items a flag-mechanism type keeps in the harness config: the
    object keys under the pointer's `<name>` slot whose own flag is `true`. An entry with
    no flag (disable would refuse: a key is never invented) or `false` by hand (enable
    would refuse: no state entry) is not something this tool can toggle, so not listed."""
    rel, pointer = h.flags[type_]
    prefix = pointer[:pointer.index("<name>")] if "<name>" in pointer else pointer[:-1]
    try:
        node = jsonc_loads((h.home / rel).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    for k in prefix:
        node = node.get(k) if isinstance(node, dict) else None
    if not isinstance(node, dict):
        return []
    return sorted(n for n, v in node.items()
                  if isinstance(v, dict) and v.get("enabled") is True and _valid_name(n))


def backup_tools(entry: dict) -> int | None:
    """Tool count of a parked MCP server, from its verbatim backup."""
    try:
        raw = json.loads(Path(entry["backup"]).read_text(encoding="utf-8"))
    except (KeyError, OSError, ValueError, TypeError):
        return None
    if isinstance(raw, dict) and isinstance(raw.get("json"), dict):   # json-backend payload
        return _entry_tools(raw["json"].get("entry"))
    if isinstance(raw, dict) and "toml" in raw:
        return len(TOOL_SECTION.findall(str(raw["toml"]))) or None
    return _entry_tools(raw)


def plugin_estimate(root: Path) -> Estimate:
    """A plugin bundle: sum of what it ships, read from its install path."""
    parts = [("skill", d.name, d / "SKILL.md") for d in sorted((root / "skills").glob("*"))
             if (d / "SKILL.md").is_file()]
    parts += [("agent", p.stem, p) for p in sorted((root / "agents").rglob("*.md"))]
    parts += [("command", p.stem, p) for p in sorted((root / "commands").rglob("*.md"))]
    ests = [file_estimate(*p) for p in parts]
    servers = 0
    try:
        data = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))
        if isinstance(data, dict):
            servers = len(data.get("mcpServers", data))
    except (OSError, ValueError):
        pass
    tokens = sum(e[0] for e in ests) + servers * mcp_estimate("claude", None)[0]
    chars = sum(e[1] or 0 for e in ests)
    counts = Counter(p[0] for p in parts)
    basis = ", ".join(f"{counts[t]} {t}s" for t in ("skill", "agent", "command") if counts[t])
    basis += f"{', ' if basis else ''}{servers} mcp" if servers else ""
    return tokens, chars, basis or "empty bundle"


def list_plugins(warn: Callable[[str], None]) -> list[dict]:
    """`claude plugin list --json` through the injectable runner; [] when unavailable."""
    exe = claude_bin()
    if not exe:
        return []
    ok, text = run_cli(exe, ["plugin", "list", "--json"])
    try:       # run_cli merges stderr into the text: skip any notice before the array
        data = json.JSONDecoder().raw_decode(text, max(text.find("["), 0))[0] if ok else None
    except ValueError:
        data = None
    if not isinstance(data, list):
        warn("could not read `claude plugin list --json`; plugin rows come from state only")
        return []
    return [p for p in data if isinstance(p, dict) and isinstance(p.get("id"), str)]


def inventory(state: dict, table: dict, warn: Callable[[str], None] = lambda m: None,
              plugins: bool = True) -> list[Item]:
    """Every togglable item once: live, then parked-only. A dir shared by several
    harnesses is ONE item under its owner with the others in `shared_with`."""
    items: list[Item] = []
    seen: set[tuple[str, str, str]] = set()
    # flag-disabled items stay in place (the config flag is off), so a live listing alone
    # would call them enabled: the state entry is what says they are off.
    flagged = {(e.get("harness"), e.get("type"), e.get("name"))
               for e in state.get("disabled", {}).values()
               if isinstance(e, dict) and e.get("mechanism") == "flag"}

    def add(h: str, t: str, n: str, enabled: bool, est: Estimate, shared=()) -> None:
        enabled = enabled and (h, t, n) not in flagged
        if (h, t, n) not in seen:
            seen.add((h, t, n))
            items.append(Item(h, t, n, enabled, est[0] if enabled else 0,
                              0 if enabled else est[0], est[1], est[2], tuple(shared)))

    for hname, h in table.items():
        if not h.home.is_dir():
            continue
        for type_ in h.types:
            if type_ in h.dirs:
                for sub in h.dirs[type_]:
                    v = dir_view(table, hname, type_, h.home, sub)
                    if v.owner != hname:
                        continue
                    shared = [s for s in v.sharers if s != hname]
                    for name in live_names(v.live, type_):
                        add(hname, type_, name, True,
                            file_estimate(type_, name, resolve_item(v.live, name)), shared)
            elif type_ in h.flags:
                for name in flag_names(h, type_):
                    add(hname, type_, name, True, mcp_estimate(hname, None) if type_ == "mcp"
                        else (0, None, "flag entry; size unknown"))
            elif type_ == "mcp":
                counts = mcp_tool_counts(h)
                for name in live_mcp(h.home, h.backend):
                    add(hname, type_, name, True, mcp_estimate(hname, counts.get(name)))

    claude = table.get("claude")
    if plugins and claude and claude.home.is_dir() and "plugin" in claude.types:
        for p in list_plugins(warn):
            root = p.get("installPath")
            add("claude", "plugin", p["id"], p.get("enabled", True) is not False,
                plugin_estimate(Path(root)) if isinstance(root, str)
                else (0, None, "no installPath"))

    for e in state.get("disabled", {}).values():
        h, t, n = e.get("harness", "claude"), e.get("type"), e.get("name")
        if not all(isinstance(x, str) and x for x in (h, t, n)):
            continue                      # hand-edited entry: doctor reports it
        if t == "mcp":
            est = mcp_estimate(h, backup_tools(e))
        elif t == "plugin":
            est = (0, None, "not in plugin list")
        else:
            parked = e.get("parked_at")
            est = file_estimate(t, n, Path(parked) if isinstance(parked, str) and parked else None)
        shared = e.get("shared_with")
        add(h, t, n, False, est,
            [x for x in shared if isinstance(x, str)] if isinstance(shared, list) else ())
    return items
