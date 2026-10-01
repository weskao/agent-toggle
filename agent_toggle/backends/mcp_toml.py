"""TOML-block MCP backend (codex config.toml): verbatim text slices."""
from __future__ import annotations

from pathlib import Path


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


def codex_mcp_remove(config: Path, name: str, dry_run: bool = False) -> str | None:
    text = config.read_text()
    span = toml_block(text, name)
    if span is None:
        return None
    lines = text.splitlines(keepends=True)
    block = "".join(lines[span[0]:span[1]])
    if dry_run:
        return block
    config.write_text("".join(lines[:span[0]] + lines[span[1]:]))
    return block


def codex_mcp_add(config: Path, block: str) -> None:
    text = config.read_text() if config.exists() else ""
    if text and not text.endswith("\n"):
        text += "\n"
    config.write_text(text + ("\n" if text else "") + block.rstrip("\n") + "\n")
