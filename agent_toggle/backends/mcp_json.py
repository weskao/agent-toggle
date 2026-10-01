"""JSON-key MCP backend: raw server entries in ~/.claude.json."""
from __future__ import annotations

import json
from pathlib import Path

from .. import fs


def claude_mcp_config(name: str) -> tuple[dict, str, str | None] | None:
    """Find a server's RAW config in ~/.claude.json -> (config, scope, project).

    `claude mcp get` prints a human summary that silently drops auth fields
    (headers, headersHelper), so a backup built from it restores a server that
    then fails with 401. The raw entry is the only lossless source.

    Two places hold one: user scope at the top level, and local scope nested
    under projects/<dir>. The project path travels with the backup because
    `claude mcp remove -s local` only sees the project it is run in -- restore
    it from the wrong directory and the server reappears in the wrong project.

    Raises LookupError when one name is local-scope in several projects and
    the cwd does not pick a winner.
    """
    try:
        cfg = json.loads(fs.claude_json().read_text())
    except (OSError, json.JSONDecodeError):
        return None

    raw = cfg.get("mcpServers", {}).get(name)
    if raw is not None:
        return raw, "user", None

    hits = [(proj, pdata["mcpServers"][name])
            for proj, pdata in cfg.get("projects", {}).items()
            if isinstance(pdata, dict) and name in (pdata.get("mcpServers") or {})]
    if not hits:
        return None
    cwd = str(Path.cwd())
    for proj, raw in hits:
        if proj == cwd:
            return raw, "local", proj
    if len(hits) > 1:
        raise LookupError(
            f"local-scope in {len(hits)} projects "
            f"({', '.join(p for p, _ in hits)}) -- cd into the one you mean")
    return hits[0][1], "local", hits[0][0]


def claudeai_connector_names() -> set[str]:
    """Names claude.ai connectors are known under (account-level, no local
    mcpServers entry -- .claudeAiMcpEverConnected is the only place they are
    listed)."""
    try:
        cfg = json.loads(fs.claude_json().read_text())
    except (OSError, json.JSONDecodeError):
        return set()
    return set(cfg.get("claudeAiMcpEverConnected", []) or [])


def toggle_claudeai_connector(action: str, name: str) -> tuple[bool, list | str]:
    """Fan a claude.ai connector on/off across every EXISTING project entry
    in ~/.claude.json's disabledMcpServers list -- the only place /mcp writes
    that state; no `claude mcp` CLI verb reaches it.

    ponytail: covers projects that exist now; a project dir opened for the
    first time later starts without the entry until this is re-run for it.
    """
    path = fs.claude_json()
    try:
        cfg = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        return False, f"~/.claude.json unreadable: {e}"

    touched = []
    for proj, pdata in cfg.get("projects", {}).items():
        if not isinstance(pdata, dict):
            continue
        cur = pdata.get("disabledMcpServers") or []
        has = name in cur
        if action == "disable" and not has:
            pdata["disabledMcpServers"] = [*cur, name]
            touched.append(proj)
        elif action == "enable" and has:
            pdata["disabledMcpServers"] = [n for n in cur if n != name]
            touched.append(proj)

    if not touched:
        return True, []
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    tmp.replace(path)
    return True, touched
