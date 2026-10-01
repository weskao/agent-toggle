"""Companion files: helpers an item references that live outside the item."""
from __future__ import annotations

import os
import re
from pathlib import Path

from . import fs
from .fs import iter_text_files, read_text, safe_move
from .output import Result

REF_RE = re.compile(
    r"""[`'"(\[]?\s*((?:\$HOME|~|\$\{?[A-Za-z_][A-Za-z0-9_]*\}?)?[\w./@-]*"""
    r"""\.(?:sh|py|mjs|cjs|js|ts|json|ya?ml|toml|txt|md))"""
)


def move(src: Path, dest_dir: Path, dry_run: bool = False) -> Path:
    """safe_move, or (dry run) the same refusals with nothing written.

    Returns where `src` lands.
    """
    if not dry_run:
        return safe_move(src, dest_dir)
    if not (src.exists() or src.is_symlink()):
        raise FileNotFoundError(f"{src} does not exist")
    if dest_dir.exists() and not dest_dir.is_dir():
        raise NotADirectoryError(f"{dest_dir} exists but is not a directory")
    target = dest_dir / src.name
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"{target} already exists -- refusing to overwrite")
    return target


def _own_scope(item: Path, source: Path | None) -> tuple[list[Path], list[Path]]:
    """(dirs, files) that count as the item itself, so they are never companions.

    `item` is where the item lives (real run) or WILL live once parked (dry run).
    A dry run also passes `source`, where it still sits, so the plan matches
    what a real run sees after the move.
    """
    real = source or item
    dirs = [item if real.is_dir() else item.parent]
    files = [item]
    if source is not None:
        (dirs if real.is_dir() else files).append(source)
    return dirs, files


def _is_own(p: Path, scope: tuple[list[Path], list[Path]]) -> bool:
    dirs, files = scope
    return p in files or any(p.is_relative_to(d) for d in dirs)


def find_companions(item: Path, home: Path, source: Path | None = None) -> list[Path]:
    """Files under `home` that `item` references and that live outside `item`.

    Deliberately a heuristic over file text: a runtime-built path cannot be
    found by reading source. Everything it finds is REPORTED before anything
    moves, so a wrong guess is visible rather than silent.
    """
    # Resolve everything into one space: ~/.claude is often a symlink, and on
    # macOS /var itself is one, so an unresolved comparison silently drops
    # every companion as "outside the harness".
    home, item = home.resolve(), item.resolve()
    source = source.resolve() if source else None
    scope = _own_scope(item, source)
    item_dir = scope[0][0]
    user_home = fs.home()
    found: set[Path] = set()
    for src in iter_text_files(source or item):
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
                if _is_own(c, scope):
                    continue             # part of the item itself
                found.add(c)
    return sorted(found)


def other_users(target: Path, item: Path, home: Path,
                source: Path | None = None) -> list[Path]:
    """Live files outside `item` that also reference `target`."""
    home, item, target = home.resolve(), item.resolve(), target.resolve()
    scope = _own_scope(item, source.resolve() if source else None)
    needle = target.name
    rel = str(target.relative_to(home))
    users: list[Path] = []
    for src in iter_text_files(home):
        if src == target or _is_own(src, scope):
            continue
        text = read_text(src)
        if needle in text or rel in text:
            users.append(src)
            if len(users) >= 3:          # enough to prove "shared"
                break
    return users


def park_companions(item: Path, home: Path, key: str, out: Result | None = None,
                    dry_run: bool = False, source: Path | None = None) -> list[dict]:
    """Move companions used ONLY by this item; report the shared ones.

    Dry run: plan the same moves without making them (`source` = where the item
    still sits).

    Moving a shared helper (tg-send.sh is referenced by five different things)
    would silently break every other user, so sharing is a veto, not a warning.
    """
    out = out or Result()
    home = home.resolve()
    moved: list[dict] = []
    for comp in find_companions(item, home, source):
        users = other_users(comp, item, home, source)
        if users:
            names = ", ".join(u.name for u in users[:2])
            out.say(f"    - kept {comp.relative_to(home)} (also used by {names})", warn=True)
            continue
        dest = fs.companion_dir() / key / comp.relative_to(home).parent
        try:
            target = move(comp, dest, dry_run)
        except (OSError, FileExistsError, NotADirectoryError) as e:
            out.say(f"    ! companion {comp.name}: {e}", warn=True)
            continue
        moved.append({"from": str(comp), "to": str(target)})
        out.say(f"    + {'would park' if dry_run else 'parked'} "
                f"{comp.relative_to(home)} (exclusive)")
    return moved


def restore_companions(entry: dict, out: Result | None = None,
                       dry_run: bool = False) -> None:
    out = out or Result()
    for c in entry.get("companions", []):
        src, dst = Path(c["to"]), Path(c["from"])
        if not src.exists():
            out.say(f"    ! companion missing: {src}", warn=True)
            continue
        try:
            move(src, dst.parent, dry_run)
        except (OSError, FileExistsError, NotADirectoryError) as e:
            out.say(f"    ! companion {dst.name}: {e}", warn=True)
        else:
            out.say(f"    + {'would restore' if dry_run else 'restored'} {dst.name}")
