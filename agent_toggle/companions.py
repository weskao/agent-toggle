"""Companion files: helpers an item references that live outside the item."""
from __future__ import annotations

import os
import re
from pathlib import Path

from . import fs
from .fs import iter_text_files, read_text, safe_move

REF_RE = re.compile(
    r"""[`'"(\[]?\s*((?:\$HOME|~|\$\{?[A-Za-z_][A-Za-z0-9_]*\}?)?[\w./@-]*"""
    r"""\.(?:sh|py|mjs|cjs|js|ts|json|ya?ml|toml|txt|md))"""
)


def find_companions(item: Path, home: Path) -> list[Path]:
    """Files under `home` that `item` references and that live outside `item`.

    Deliberately a heuristic over file text: a runtime-built path cannot be
    found by reading source. Everything it finds is REPORTED before anything
    moves, so a wrong guess is visible rather than silent.
    """
    # Resolve everything into one space: ~/.claude is often a symlink, and on
    # macOS /var itself is one, so an unresolved comparison silently drops
    # every companion as "outside the harness".
    home, item = home.resolve(), item.resolve()
    user_home = fs.home()
    found: set[Path] = set()
    item_dir = item if item.is_dir() else item.parent
    for src in iter_text_files(item):
        for raw in REF_RE.findall(read_text(src)):
            token = raw.strip("`'\"([ ")
            # Anchor on fs.home(), the same home the harness table resolves
            # from, so `~` and $HOME agree with the harness being operated on.
            if token.startswith("~"):
                token = str(user_home) + token[1:]
            token = token.replace("${HOME}", str(user_home)).replace("$HOME", str(user_home))
            expanded = os.path.expandvars(token)
            if "$" in expanded:          # unresolved variable -- cannot verify
                continue
            cands = ([Path(expanded)] if expanded.startswith("/")
                     else [home / expanded, item_dir / expanded])
            for c in cands:
                try:
                    c = c.resolve()
                except OSError:
                    continue
                if not c.is_file() or not c.is_relative_to(home):
                    continue
                if c == item or c.is_relative_to(item_dir):
                    continue             # part of the item itself
                found.add(c)
    return sorted(found)


def other_users(target: Path, item: Path, home: Path) -> list[Path]:
    """Live files outside `item` that also reference `target`."""
    home, item, target = home.resolve(), item.resolve(), target.resolve()
    needle = target.name
    rel = str(target.relative_to(home))
    users: list[Path] = []
    item_dir = item if item.is_dir() else item.parent
    for src in iter_text_files(home):
        if src == target or src.is_relative_to(item_dir):
            continue
        text = read_text(src)
        if needle in text or rel in text:
            users.append(src)
            if len(users) >= 3:          # enough to prove "shared"
                break
    return users


def park_companions(item: Path, home: Path, key: str) -> list[dict]:
    """Move companions used ONLY by this item; report the shared ones.

    Moving a shared helper (tg-send.sh is referenced by five different things)
    would silently break every other user, so sharing is a veto, not a warning.
    """
    home = home.resolve()
    moved: list[dict] = []
    for comp in find_companions(item, home):
        users = other_users(comp, item, home)
        if users:
            names = ", ".join(u.name for u in users[:2])
            print(f"    - kept {comp.relative_to(home)} (also used by {names})")
            continue
        dest = fs.companion_dir() / key / comp.relative_to(home).parent
        try:
            target = safe_move(comp, dest)
        except (OSError, FileExistsError, NotADirectoryError) as e:
            print(f"    ! companion {comp.name}: {e}")
            continue
        moved.append({"from": str(comp), "to": str(target)})
        print(f"    + parked {comp.relative_to(home)} (exclusive)")
    return moved


def restore_companions(entry: dict) -> None:
    for c in entry.get("companions", []):
        src, dst = Path(c["to"]), Path(c["from"])
        if not src.exists():
            print(f"    ! companion missing: {src}")
            continue
        try:
            safe_move(src, dst.parent)
        except (OSError, FileExistsError, NotADirectoryError) as e:
            print(f"    ! companion {dst.name}: {e}")
        else:
            print(f"    + restored {dst.name}")
