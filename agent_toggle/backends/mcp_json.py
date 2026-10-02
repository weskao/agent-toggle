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
        cfg = json.loads(fs.claude_json().read_text(encoding="utf-8"))
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
        cfg = json.loads(fs.claude_json().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return set(cfg.get("claudeAiMcpEverConnected", []) or [])


def toggle_claudeai_connector(action: str, name: str,
                              dry_run: bool = False) -> tuple[bool, list | str]:
    """Fan a claude.ai connector on/off across every EXISTING project entry
    in ~/.claude.json's disabledMcpServers list -- the only place /mcp writes
    that state; no `claude mcp` CLI verb reaches it.

    ponytail: covers projects that exist now; a project dir opened for the
    first time later starts without the entry until this is re-run for it.
    """
    path = fs.claude_json()
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
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

    if not touched or dry_run:
        return True, touched
    text = json.dumps(cfg, indent=2, ensure_ascii=False)
    try:
        fs.checked_write(path, text, fs.json_verify(text))
    except fs.WriteError as e:                # rolled back to the previous bytes
        return False, str(e)
    return True, touched


# ---------------------------------------------------- project <dir>/.mcp.json
# The file is repo-controlled text: strict JSON only (no JSONC/BOM/duplicate keys),
# never edited through a symlink, never larger than MAX_PROJECT_MCP.

MAX_PROJECT_MCP = 4 << 20


class ProjectMcpError(Exception):
    """A .mcp.json that must not be edited; the message is the error row detail."""


def _no_dup(pairs: list) -> dict:
    out: dict = {}
    for k, v in pairs:
        if k in out:
            raise ValueError(f"duplicate key {k!r}")
        out[k] = v
    return out


def _not_json(const: str):
    raise ValueError(f"{const} is not JSON")


def _finite(num: str) -> float:
    f = float(num)
    if f in (float("inf"), float("-inf")):
        raise ValueError(f"{num} is out of range")
    return f


def read_project_mcp(file: Path) -> tuple[str, dict]:
    """(exact text, parsed object) of a project .mcp.json, or ProjectMcpError."""
    if file.is_symlink():
        raise ProjectMcpError(f"{file} is a symlink -- refusing to edit through it")
    try:
        if file.stat().st_size > MAX_PROJECT_MCP:
            raise ProjectMcpError(f"{file} is larger than {MAX_PROJECT_MCP >> 20} MiB")
        text = file.read_bytes().decode("utf-8")      # bytes: CRLF must survive
    except FileNotFoundError:
        raise ProjectMcpError(f"{file} not found") from None
    except (OSError, UnicodeDecodeError) as e:
        raise ProjectMcpError(f"{file} unreadable: {e}") from None
    try:
        obj = json.loads(text, object_pairs_hook=_no_dup, parse_constant=_not_json,
                         parse_float=_finite)
        json.dumps(obj, ensure_ascii=False).encode("utf-8")    # lone surrogates
    except (ValueError, RecursionError) as e:
        raise ProjectMcpError(f"{file} is not strict JSON ({e}) -- refusing "
                              f"(JSONC, BOM, trailing commas and duplicate keys are not supported)"
                              ) from None
    if not isinstance(obj, dict) or not isinstance(obj.get("mcpServers"), dict):
        raise ProjectMcpError(f"{file} has no mcpServers object")
    return text, obj


def _dump_like(obj: dict, text: str) -> str:
    """Serialise `obj` in the layout of `text` (tab/2-space indent, CRLF, final newline)."""
    out = json.dumps(obj, indent="\t" if "\n\t" in text else 2 if "\n" in text else None,
                     ensure_ascii=False)
    if text.endswith("\n"):
        out += "\n"
    return out.replace("\n", "\r\n") if "\r\n" in text else out


def project_mcp_remove(file: Path, name: str) -> tuple[dict, str, str]:
    """Plan the removal of `name`: (its raw entry, text before, text after). Writes nothing."""
    text, obj = read_project_mcp(file)
    if name not in obj["mcpServers"]:
        raise ProjectMcpError(f"no mcpServers.{name} in {file}")
    raw = obj["mcpServers"].pop(name)
    return raw, text, _dump_like(obj, text)


def project_mcp_restore(file: Path, name: str, payload: dict) -> tuple[str, str]:
    """Plan putting `name` back: (current text, new text). The new text is the exact
    pre-disable text when the file is still as we left it, else the entry merged into
    the current file. Writes nothing."""
    text, obj = read_project_mcp(file)
    if name in obj["mcpServers"]:
        raise ProjectMcpError(f"mcpServers.{name} is already defined in {file} -- "
                              f"remove it first, the backup stays in state")
    if text == payload["after"]:
        return text, payload["before"]
    obj["mcpServers"][name] = payload["entry"]
    return text, _dump_like(obj, text)


def write_project_mcp(file: Path, text: str, expect_before: str) -> None:
    """Replace the file with `text` (the whole planned result); fs.WriteError, with
    the previous bytes and mode restored, when the file is no longer `expect_before`
    (an editor saved between plan and write) or the written result is not exactly `text`."""
    check = fs.json_verify(text)

    def verify(before: str, after: str) -> str:
        if before != expect_before:
            raise ValueError("the file changed while it was being edited")
        return check(before, after)

    fs.checked_write(file, text, verify)
