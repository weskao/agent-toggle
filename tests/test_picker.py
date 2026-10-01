"""Picker data gathering and filtering (no curses screen is opened)."""
from __future__ import annotations

import os
import unittest

from base import SandboxCase

from agent_toggle.harnesses import build

try:
    from agent_toggle.ui import picker
except ImportError:              # no curses build (Windows without windows-curses)
    picker = None


@unittest.skipIf(picker is None, "curses unavailable")
class PickerTest(SandboxCase):
    @unittest.skipIf(os.name == "nt", "live_names joins nesting with os.sep; Windows port pending")
    def test_live_names_addresses_nesting_with_colon(self) -> None:
        self.write("commands/google.md")
        self.write("commands/news-briefing/ai.md")
        self.write("skills/solo/SKILL.md")
        names = picker.live_names(self.home / "commands", "command")
        # flat command listed
        self.assertIn("google", names)
        # nested command uses a colon
        self.assertIn("news-briefing:ai", names)
        # skills listed as directories
        self.assertEqual(picker.live_names(self.home / "skills", "skill"), ["solo"])
        # missing dir yields nothing
        self.assertEqual(picker.live_names(self.home / "nope", "skill"), [])

    def test_live_mcp_reads_toml_server_names(self) -> None:
        (self.home / "config.toml").write_text(
            '[mcp_servers.alpha]\ncommand = "a"\n'
            "[mcp_servers.alpha.tools.x]\nenabled = true\n"
            '[mcp_servers.beta]\ncommand = "b"\n[other]\nk = 1\n'
        )
        names = picker.live_mcp(self.home, "toml")
        # toml servers listed once each
        self.assertEqual(names, ["alpha", "beta"])
        # sub-tables are not mistaken for servers
        self.assertNotIn("alpha.tools.x", names)

    def test_collect_merges_live_and_parked(self) -> None:
        self.write("skills/live-one/SKILL.md")
        state = {"version": 2, "disabled": {
            "claude:skill:parked-one": {"harness": "claude", "type": "skill",
                                        "name": "parked-one", "at": "2026-09-19"},
            "claude:plugin:x": {"harness": "claude", "type": "plugin",
                                "name": "x", "at": "2026-09-19"},
        }}
        rows = picker.collect(state, {"claude": build(self.tmp)["claude"]})
        by_name = {r.name: r for r in rows}
        # live item present and ticked
        self.assertIs(by_name["live-one"].enabled, True)
        # parked item present and unticked
        self.assertIs(by_name["parked-one"].enabled, False)
        # plugins stay out of the picker
        self.assertNotIn("x", by_name)

    def test_match_filters_on_all_terms(self) -> None:
        rows = [picker.Row("claude", "command", "orch:batch", True),
                picker.Row("codex", "skill", "orch-helper", True),
                picker.Row("claude", "skill", "unrelated", True)]
        # single term filters
        self.assertEqual(len(picker.match(rows, "orch")), 2)
        # terms are ANDed
        self.assertEqual(len(picker.match(rows, "orch claude")), 1)
        # match is case-insensitive
        self.assertEqual(len(picker.match(rows, "ORCH")), 2)
        # empty query returns everything
        self.assertEqual(len(picker.match(rows, "")), 3)

    def test_row_tracks_staged_change(self) -> None:
        r = picker.Row("claude", "skill", "demo", True)
        # unchanged row reports no change
        self.assertFalse(r.changed)
        r.staged = False
        # unticking marks a change
        self.assertTrue(r.changed)
        # label is harness/type/name
        self.assertEqual(r.label, "claude/skill/demo")
