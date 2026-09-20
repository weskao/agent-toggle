#!/usr/bin/env python3
"""Tests for agent_toggle. stdlib only, no fixtures, no network.

Run: python3 test_agent_toggle.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import agent_toggle as at

PASS = FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {detail}")


class Sandbox:
    """A throwaway $HOME with a fake harness tree and redirected state dirs."""

    def __init__(self, harness: str = "claude"):
        self.harness = harness
        self.tmp = Path(tempfile.mkdtemp(prefix="agent-toggle-test-"))
        self.home = self.tmp / f".{harness}"
        for sub in ("skills", "agents", "commands", "scripts"):
            (self.home / sub).mkdir(parents=True)
        self._saved = {k: getattr(at, k) for k in
                       ("HOME", "STATE_DIR", "STATE_FILE", "LOG_FILE",
                        "BACKUP_DIR", "COMPANION_DIR", "HARNESSES")}
        at.HOME = self.tmp
        at.STATE_DIR = self.tmp / ".agent-toggle"
        at.STATE_FILE = at.STATE_DIR / "state.json"
        at.LOG_FILE = at.STATE_DIR / "log.jsonl"
        at.BACKUP_DIR = at.STATE_DIR / "mcp-backups"
        at.COMPANION_DIR = at.STATE_DIR / "companions"
        at.HARNESSES = {harness: (self.home,
                                  ("skill", "agent", "command", "plugin", "mcp"),
                                  "toml")}

    def write(self, rel: str, text: str = "x") -> Path:
        p = self.home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def close(self) -> None:
        for k, v in self._saved.items():
            setattr(at, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)


# --------------------------------------------------------------- safe_move

def test_never_renames_source_into_dest() -> None:
    """The bug this guard exists for: `mv X dest/` renames X to dest."""
    sb = Sandbox()
    try:
        src = sb.write("skills/demo/SKILL.md", "hello")
        dest = sb.home / "skills-disabled"
        assert not dest.exists()
        at.safe_move(src.parent, dest)
        check("safe_move keeps the item as a CHILD of dest",
              (dest / "demo" / "SKILL.md").read_text() == "hello")
        check("safe_move did not rename src to dest", dest.is_dir())
    finally:
        sb.close()


def test_refuses_file_as_dest_dir() -> None:
    sb = Sandbox()
    try:
        src = sb.write("skills/a/SKILL.md")
        blocker = sb.write("skills-disabled", "i am a file")
        try:
            at.safe_move(src.parent, blocker)
            check("safe_move refuses a file as dest", False, "no raise")
        except NotADirectoryError:
            check("safe_move refuses a file as dest", True)
    finally:
        sb.close()


def test_refuses_overwrite() -> None:
    sb = Sandbox()
    try:
        src = sb.write("skills/a/SKILL.md", "new")
        sb.write("skills-disabled/a/SKILL.md", "old")
        try:
            at.safe_move(src.parent, sb.home / "skills-disabled")
            check("safe_move refuses to overwrite", False, "no raise")
        except FileExistsError:
            check("safe_move refuses to overwrite", True)
            check("existing parked copy untouched",
                  (sb.home / "skills-disabled/a/SKILL.md").read_text() == "old")
    finally:
        sb.close()


# ------------------------------------------------------------ nested paths

def test_nested_command_roundtrip() -> None:
    """`orch:batch` lives at commands/orch/batch.md and must come back there."""
    sb = Sandbox()
    try:
        sb.write("commands/orch/batch.md", "batch")
        sb.write("commands/other/batch.md", "other")
        state = {"version": 2, "disabled": {}}

        at.toggle_dir_type("disable", "command", ["orch:batch"], state,
                           "claude", sb.home)
        check("nested command parked with its parent dir",
              (sb.home / "commands-disabled/orch/batch.md").is_file())
        check("sibling with same basename untouched",
              (sb.home / "commands/other/batch.md").read_text() == "other")
        check("origin recorded", state["disabled"]["claude:command:orch:batch"]
              ["origin"].endswith("commands/orch/batch.md"))

        at.toggle_dir_type("enable", "command", ["orch:batch"], state,
                           "claude", sb.home)
        check("nested command restored to its original path",
              (sb.home / "commands/orch/batch.md").read_text() == "batch")
        check("state entry cleared", not state["disabled"])
        check("empty park subdir pruned",
              not (sb.home / "commands-disabled/orch").exists())
        check("park root kept", (sb.home / "commands-disabled").is_dir())
    finally:
        sb.close()


def test_resolve_probes_shapes() -> None:
    sb = Sandbox()
    try:
        sb.write("skills/dir-skill/SKILL.md")
        sb.write("agents/file-agent.md")
        check("resolves a directory skill",
              at.resolve_item(sb.home / "skills", "dir-skill").is_dir())
        check("resolves an .md agent without naming the suffix",
              at.resolve_item(sb.home / "agents", "file-agent").suffix == ".md")
        check("missing item resolves to None",
              at.resolve_item(sb.home / "agents", "nope") is None)
    finally:
        sb.close()


# -------------------------------------------------------------- companions

def test_exclusive_companion_moves_shared_one_stays() -> None:
    sb = Sandbox()
    try:
        sb.write("scripts/only-mine.sh", "#!/bin/sh")
        sb.write("scripts/shared.sh", "#!/bin/sh")
        sb.write("skills/solo/SKILL.md",
                 "run $HOME/.claude/scripts/only-mine.sh and scripts/shared.sh")
        sb.write("skills/other/SKILL.md", "I also call scripts/shared.sh")
        state = {"version": 2, "disabled": {}}

        at.toggle_dir_type("disable", "skill", ["solo"], state, "claude", sb.home)
        entry = state["disabled"]["claude:skill:solo"]
        moved = [Path(c["from"]).name for c in entry["companions"]]

        check("exclusive companion parked", "only-mine.sh" in moved)
        check("exclusive companion gone from live tree",
              not (sb.home / "scripts/only-mine.sh").exists())
        check("shared companion NOT parked", "shared.sh" not in moved)
        check("shared companion still live",
              (sb.home / "scripts/shared.sh").is_file())

        at.toggle_dir_type("enable", "skill", ["solo"], state, "claude", sb.home)
        check("companion restored on enable",
              (sb.home / "scripts/only-mine.sh").is_file())
    finally:
        sb.close()


def test_companion_scan_ignores_item_internals() -> None:
    sb = Sandbox()
    try:
        sb.write("skills/kit/SKILL.md", "see helper.py in this folder")
        sb.write("skills/kit/helper.py", "print(1)")
        comps = at.find_companions(sb.home / "skills/kit", sb.home)
        check("files inside the item are not companions", comps == [], str(comps))
    finally:
        sb.close()


# --------------------------------------------------------------- mcp: toml

def test_toml_block_spans_subtables() -> None:
    text = (
        "[mcp_servers.alpha]\n"
        'command = "a"\n'
        "\n"
        "[mcp_servers.beta]\n"
        'command = "b"\n'
        "[mcp_servers.beta.tools.thing]\n"
        "enabled = true\n"
        "\n"
        "[other]\n"
        "k = 1\n"
    )
    span = at.toml_block(text, "beta")
    block = "".join(text.splitlines(keepends=True)[span[0]:span[1]])
    check("toml block includes its sub-tables",
          "beta.tools.thing" in block and "enabled = true" in block)
    check("toml block stops before the next top-level table",
          "[other]" not in block)
    check("toml block excludes the previous server", "alpha" not in block)
    check("unknown server returns None", at.toml_block(text, "nope") is None)


def test_toml_mcp_roundtrip() -> None:
    sb = Sandbox()
    try:
        cfg = sb.home / "config.toml"
        cfg.write_text('[general]\nx = 1\n\n[mcp_servers.tg]\ncommand = "tg"\n'
                       '[mcp_servers.tg.tools.send]\nenabled = true\n')
        block = at.codex_mcp_remove(cfg, "tg")
        check("removal drops the server from config", "mcp_servers.tg" not in cfg.read_text())
        check("removal keeps unrelated tables", "[general]" in cfg.read_text())
        at.codex_mcp_add(cfg, block)
        check("re-add restores the server", "[mcp_servers.tg]" in cfg.read_text())
        check("re-add restores sub-tables", "tg.tools.send" in cfg.read_text())
    finally:
        sb.close()


# ----------------------------------------------------------------- migrate

def test_migrate_imports_legacy_state() -> None:
    sb = Sandbox()
    try:
        legacy = sb.tmp / ".claude-toggle"
        (legacy / "mcp-backups").mkdir(parents=True)
        backup = legacy / "mcp-backups/telegram-mcp.json"
        backup.write_text('{"type": "http", "url": "http://127.0.0.1:8765/mcp"}')
        (legacy / "state.json").write_text(json.dumps({"version": 1, "disabled": {
            "mcp:telegram-mcp": {"type": "mcp", "name": "telegram-mcp",
                                 "backup": str(backup), "at": "2026-09-19T00:00:00+0800"},
        }}))
        at.LEGACY_STATE_DIR = legacy
        state = {"version": 2, "disabled": {}}
        at.cmd_migrate(state)

        entry = state["disabled"].get("claude:mcp:telegram-mcp")
        check("legacy entry imported under a harness key", entry is not None)
        check("imported entry is tagged claude", entry and entry["harness"] == "claude")
        check("backup copied into the new store",
              entry and Path(entry["backup"]).is_file())
        check("legacy backup left in place", backup.is_file())
    finally:
        at.LEGACY_STATE_DIR = at.HOME / ".claude-toggle"
        sb.close()


# ------------------------------------------------------------ harness gate

def test_unsupported_pair_is_refused() -> None:
    sb = Sandbox()
    try:
        at.HARNESSES = {"grok": (sb.home, ("skill",), None)}
        try:
            at.main(["disable", "mcp", "whatever", "--harness", "grok"])
            check("unsupported type is refused", False, "no SystemExit")
        except SystemExit as e:
            check("unsupported type is refused", e.code == 1)
        try:
            at.main(["disable", "skill", "x", "--harness", "nope"])
            check("unknown harness is refused", False, "no SystemExit")
        except SystemExit as e:
            check("unknown harness is refused", e.code == 1)
    finally:
        sb.close()


def test_mcp_without_backend_fails_loudly() -> None:
    sb = Sandbox()
    try:
        state = {"version": 2, "disabled": {}}
        fails = at.toggle_mcp("disable", ["x"], state, "openclaw", sb.home, None)
        check("sqlite-backed harness reports failure instead of no-op", fails == 1)
        check("no state written for the refused server", not state["disabled"])
    finally:
        sb.close()


# ---------------------------------------------------------------------- ui

def test_live_names_addresses_nesting_with_colon() -> None:
    import ui
    sb = Sandbox()
    try:
        sb.write("commands/google.md")
        sb.write("commands/news-briefing/ai.md")
        sb.write("skills/solo/SKILL.md")
        names = ui.live_names(sb.home / "commands", "command")
        check("flat command listed", "google" in names)
        check("nested command uses a colon", "news-briefing:ai" in names, str(names))
        check("skills listed as directories",
              ui.live_names(sb.home / "skills", "skill") == ["solo"])
        check("missing dir yields nothing",
              ui.live_names(sb.home / "nope", "skill") == [])
    finally:
        sb.close()


def test_live_mcp_reads_toml_server_names() -> None:
    import ui
    sb = Sandbox()
    try:
        (sb.home / "config.toml").write_text(
            '[mcp_servers.alpha]\ncommand = "a"\n'
            "[mcp_servers.alpha.tools.x]\nenabled = true\n"
            '[mcp_servers.beta]\ncommand = "b"\n[other]\nk = 1\n'
        )
        names = ui.live_mcp(sb.home, "toml")
        check("toml servers listed once each", names == ["alpha", "beta"], str(names))
        check("sub-tables are not mistaken for servers", "alpha.tools.x" not in names)
    finally:
        sb.close()


def test_collect_merges_live_and_parked() -> None:
    import ui
    sb = Sandbox()
    try:
        sb.write("skills/live-one/SKILL.md")
        state = {"version": 2, "disabled": {
            "claude:skill:parked-one": {"harness": "claude", "type": "skill",
                                        "name": "parked-one", "at": "2026-09-19"},
            "claude:plugin:x": {"harness": "claude", "type": "plugin",
                                "name": "x", "at": "2026-09-19"},
        }}
        rows = ui.collect(state, {"claude": (sb.home, ("skill",), None)},
                          {"skill": "skills"})
        by_name = {r.name: r for r in rows}
        check("live item present and ticked",
              by_name["live-one"].enabled is True)
        check("parked item present and unticked",
              by_name["parked-one"].enabled is False)
        check("plugins stay out of the picker", "x" not in by_name)
    finally:
        sb.close()


def test_match_filters_on_all_terms() -> None:
    import ui
    rows = [ui.Row("claude", "command", "orch:batch", True),
            ui.Row("codex", "skill", "orch-helper", True),
            ui.Row("claude", "skill", "unrelated", True)]
    check("single term filters", len(ui.match(rows, "orch")) == 2)
    check("terms are ANDed", len(ui.match(rows, "orch claude")) == 1)
    check("match is case-insensitive", len(ui.match(rows, "ORCH")) == 2)
    check("empty query returns everything", len(ui.match(rows, "")) == 3)


def test_row_tracks_staged_change() -> None:
    import ui
    r = ui.Row("claude", "skill", "demo", True)
    check("unchanged row reports no change", not r.changed)
    r.staged = False
    check("unticking marks a change", r.changed)
    check("label is harness/type/name", r.label == "claude/skill/demo")


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print(f"\n{t.__name__}")
        t()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
