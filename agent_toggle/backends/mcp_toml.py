"""TOML-block MCP backend (codex config.toml): verbatim text slices."""
from __future__ import annotations

import re
from pathlib import Path

from .. import fs


def toml_block(text: str, name: str) -> tuple[int, int] | None:
    """Line span of `[mcp_servers.<name>]` and its sub-tables, or None.

    Returned as a slice so the backup is the VERBATIM source text. Python's
    stdlib reads TOML (tomllib) but cannot write it, and hand-rolling a
    serializer would mangle comments and formatting -- a text slice is both
    lossless and far less code.
    """
    lines = text.splitlines(keepends=True)
    head = f"[mcp_servers.{name}]"
    sub = f"[mcp_servers.{name}."
    start = end = None
    for i, line in enumerate(lines):
        s = line.strip()
        if s == head:
            start = i
            end = len(lines)
        elif start is not None and s.startswith("["):
            if not s.startswith(sub):
                end = i
                break
    return None if start is None else (start, end)


def remove_verify(text: str, name: str, block: str):
    """verify() for a removal planned against `text`: the file is still exactly what we
    read (no concurrent edit), and only that block is gone. The note says
    `unverified (no tomllib)` when no TOML parser could also parse the result."""
    parse = fs.toml_verify()

    def verify(before: str, after: str) -> str:
        if before != text:
            raise ValueError("file changed since it was read")
        if after != before.replace(block, "", 1) or toml_block(after, name) is not None:
            raise ValueError("result differs from the one-block removal")
        return parse(before, after)
    return verify


def add_verify(text: str, block: str):
    """verify() for appending `block` to `text` (see remove_verify)."""
    parse = fs.toml_verify()

    def verify(before: str, after: str) -> str:
        if before != text:
            raise ValueError("file changed since it was read")
        if not (after.startswith(before) and after.endswith(block.rstrip("\n") + "\n")):
            raise ValueError("result differs from the one-block append")
        return parse(before, after)
    return verify


def _read(config: Path) -> str:
    """The file's exact text: read_text would turn CRLF into LF and break byte-identity."""
    return config.read_bytes().decode("utf-8")


def codex_mcp_remove(config: Path, name: str, dry_run: bool = False) -> str | None:
    text = _read(config)
    span = toml_block(text, name)
    if span is None:
        return None
    lines = text.splitlines(keepends=True)
    block = "".join(lines[span[0]:span[1]])
    if dry_run:
        return block
    after = "".join(lines[:span[0]] + lines[span[1]:])
    fs.checked_write(config, after, remove_verify(text, name, block))   # WriteError, rolled back
    return block


def codex_mcp_add(config: Path, block: str) -> None:
    text = _read(config) if config.exists() else ""
    read = text
    if text and not text.endswith("\n"):
        text += "\n"
    # Removal leaves the blank line that separated the block from its neighbour;
    # not adding a second one makes disable -> enable byte-identical for a last block.
    sep = "" if not text or re.search(r"\n\r?\n\Z", text) else "\n"
    after = text + sep + block.rstrip("\n") + "\n"
    fs.checked_write(config, after, add_verify(read, block))   # raises fs.WriteError, rolled back
