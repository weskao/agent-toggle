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
        obj = _strict_loads(text)
    except (ValueError, RecursionError) as e:
        raise ProjectMcpError(f"{file} is not strict JSON ({e}) -- refusing "
                              f"(JSONC, BOM, trailing commas and duplicate keys are not supported)"
                              ) from None
    if not isinstance(obj, dict) or not isinstance(obj.get("mcpServers"), dict):
        raise ProjectMcpError(f"{file} has no mcpServers object")
    return text, obj


def _strict_loads(text: str):
    """json.loads that refuses what read_project_mcp refuses (ValueError)."""
    obj = json.loads(text, object_pairs_hook=_no_dup, parse_constant=_not_json,
                     parse_float=_finite)
    json.dumps(obj, ensure_ascii=False).encode("utf-8")        # lone surrogates
    return obj


def _dump_like(obj: dict, text: str) -> str:
    """Serialise `obj` in the layout of `text` (tab/2-space indent, CRLF, final newline)."""
    out = json.dumps(obj, indent="\t" if "\n\t" in text else 2 if "\n" in text else None,
                     ensure_ascii=False)
    if text.endswith("\n"):
        out += "\n"
    return out.replace("\n", "\r\n") if "\r\n" in text else out


def project_mcp_remove(file: Path, name: str) -> tuple[dict, str, str]:
    """Plan the removal of `name`: (its raw entry, text before, text after). Writes nothing.
    Only the entry's own bytes (and one separator) are cut; the rest of the file stays."""
    text, obj = read_project_mcp(file)
    if name not in obj["mcpServers"]:
        raise ProjectMcpError(f"no mcpServers.{name} in {file}")
    return obj["mcpServers"][name], text, _cut(text, name)[0]


def project_mcp_backup(text: str, name: str) -> dict:
    """The backup payload for removing `name` from `text` (G15): the entry, its exact
    text with the one separator cut beside it (`cut`), and its neighbours' names
    (`prev` / `next`) -- never another server's entry or the whole file."""
    _, cut, prev, nxt = _cut(text, name)
    entry = json.loads(text, object_pairs_hook=_no_dup)["mcpServers"][name]
    return {"name": name, "entry": entry, "cut": cut, "prev": prev, "next": nxt}


def project_mcp_restore(file: Path, name: str, payload: dict) -> tuple[str, str]:
    """Plan putting `name` back: (current text, new text). Writes nothing.

    New backups (`cut`): the entry's text goes back into the CURRENT file, every other
    byte kept -- exactly where it was cut when its neighbours are still there (so an
    unchanged file comes back byte for byte), else after `prev`, else at the end.
    Old backups (`before` / `after`, whole file text): the exact pre-disable text when
    the file is still as we left it, else the entry merged into the current file."""
    text, obj = read_project_mcp(file)
    if name in obj["mcpServers"]:
        raise ProjectMcpError(f"mcpServers.{name} is already defined in {file} -- "
                              f"remove it first, the backup stays in state")
    if "cut" in payload:
        new = _insert(text, payload)
    elif text == payload["after"]:
        new = payload["before"]
    else:
        obj["mcpServers"][name] = payload["entry"]
        new = _dump_like(obj, text)
    try:
        one_entry_added(text, new, name, payload["entry"])
    except ValueError as e:
        raise ProjectMcpError(f"refused: the backup does not restore only "
                              f"mcpServers.{name} ({e})") from None
    return text, new


def one_entry_added(without: str, with_: str, name: str, entry) -> None:
    """ValueError unless `with_` is `without` plus mcpServers.<name> == `entry` and
    nothing else: every other entry and top-level key unchanged."""
    try:            # strict both sides: a restore never writes a file we would refuse
        small, big = _strict_loads(without), _strict_loads(with_)
    except RecursionError:
        raise ValueError("nested too deep") from None
    servers = big.get("mcpServers") if isinstance(big, dict) else None
    if not isinstance(servers, dict) or servers.pop(name, None) != entry:
        raise ValueError(f"mcpServers.{name} is not the backed-up entry")
    if big != small:
        raise ValueError("other content changed")


# Byte-preserving edits: walk the (already strict-JSON-checked) text for spans.
_WS = json.decoder.WHITESPACE
_DECODER = json.JSONDecoder()


def _members(text: str, i: int) -> tuple[list[tuple[str, int, int]], int]:
    """The object whose `{` is at text[i]: ([(key, key start, value end)], index of `}`)."""
    out = []
    i = _WS.match(text, i + 1).end()
    while text[i] != "}":
        start = i
        key, i = json.decoder.scanstring(text, i + 1)
        i = _WS.match(text, _WS.match(text, i).end() + 1).end()        # past ':'
        i = _DECODER.raw_decode(text, i)[1]
        out.append((key, start, i))
        i = _WS.match(text, i).end()
        if text[i] == ",":
            i = _WS.match(text, i + 1).end()
    return out, i


def _servers(text: str) -> tuple[int, list[tuple[str, int, int]], int]:
    """(index of mcpServers' `{`, its members, index of its `}`)."""
    i = json.decoder.scanstring(text, _servers_key(text) + 1)[1]
    start = _WS.match(text, _WS.match(text, i).end() + 1).end()        # past ':'
    return (start, *_members(text, start))


def _servers_key(text: str) -> int:
    top, _ = _members(text, _WS.match(text, 0).end())
    return next(s for k, s, _ in top if k == "mcpServers")


def _cut(text: str, name: str) -> tuple[str, str, str | None, str | None]:
    """(text without `name`, the cut bytes, previous key, next key). The cut is the
    member plus the separator after it -- or before it when it is the last one, or
    all of the object's inside when it is the only one -- so the rest stays put."""
    open_, ms, close = _servers(text)
    i = [k for k, _, _ in ms].index(name)
    if i + 1 < len(ms):
        a, b = ms[i][1], ms[i + 1][1]
    elif i:
        a, b = ms[i - 1][2], ms[i][2]
    else:
        a, b = open_ + 1, close
    return (text[:a] + text[b:], text[a:b], ms[i - 1][0] if i else None,
            ms[i + 1][0] if i + 1 < len(ms) else None)


def _ws_before(text: str, i: int) -> str:
    j = i
    while j and text[j - 1] in " \t\r\n":
        j -= 1
    return text[j:i]


def _insert(text: str, payload: dict) -> str:
    """`text` with the backed-up member put back (see project_mcp_restore)."""
    open_, ms, _ = _servers(text)
    keys = [k for k, _, _ in ms]
    cut = payload["cut"]
    if "\n" in text:                          # else every line end was in the cut: keep it
        cut = cut.replace("\r\n", "\n").replace("\n", "\r\n" if "\r\n" in text else "\n")
    prev, nxt = payload["prev"], payload["next"]
    if nxt in keys and (keys[keys.index(nxt) - 1] if keys.index(nxt) else None) == prev:
        at, add = ms[keys.index(nxt)][1], cut              # exact inverse of _cut
    elif nxt is None and prev is not None and keys[-1:] == [prev]:
        at, add = ms[-1][2], cut
    elif not keys and prev is None and nxt is None:
        at, add = open_ + 1, cut
    else:                     # neighbours changed: re-indent like the file, keep the order
        member = cut.strip(", \t\r\n")
        if not keys:
            ws = _ws_before(text, _servers_key(text))
            inner = ws + ws[ws.rfind("\n") + 1:] if "\n" in ws else ""
            at, add = open_ + 1, inner + member + ws if inner else member
        else:
            sep = _ws_before(text, ms[-1][1])
            if prev in keys:
                at, add = ms[keys.index(prev)][2], "," + sep + member
            elif prev is None:
                at, add = ms[0][1], member + "," + sep
            else:
                at, add = ms[-1][2], "," + sep + member
    return text[:at] + add + text[at:]


def write_project_mcp(file: Path, text: str, expect_before: str,
                      name: str | None = None, entry=None) -> None:
    """Replace the file with `text` (the whole planned result); fs.WriteError, with
    the previous bytes and mode restored, when the file is no longer `expect_before`
    (an editor saved between plan and write) or the written result is not exactly `text`.
    With `name`, the result must also differ from the file before by exactly
    mcpServers.<name> == `entry`, added or removed, and nothing else."""
    check = fs.json_verify(text)
    adding = name is not None and name in json.loads(text)["mcpServers"]

    def verify(before: str, after: str) -> str:
        if before != expect_before:
            raise ValueError("the file changed while it was being edited")
        note = check(before, after)
        if name is not None:
            one_entry_added(*((before, after) if adding else (after, before)), name, entry)
        return note

    fs.checked_write(file, text, verify)
