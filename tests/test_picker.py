"""Picker data gathering and filtering (no curses screen is opened)."""
from __future__ import annotations

import unittest
from unittest import mock

from base import SandboxCase

from agent_toggle import mechanisms
from agent_toggle.harnesses import build

try:
    from agent_toggle.ui import picker
except ImportError:              # no curses build (Windows without windows-curses)
    picker = None


@unittest.skipIf(picker is None, "curses unavailable")
class PickerTest(SandboxCase):
    def test_live_names_addresses_nesting_with_colon(self) -> None:
        self.write("commands/google.md")
        self.write("commands/news-briefing/ai.md")
        self.write("skills/solo/SKILL.md")
        names = mechanisms.live_names(self.home / "commands", "command")
        # flat command listed
        self.assertIn("google", names)
        # nested command uses a colon
        self.assertIn("news-briefing:ai", names)
        # skills listed as directories
        self.assertEqual(mechanisms.live_names(self.home / "skills", "skill"), ["solo"])
        # missing dir yields nothing
        self.assertEqual(mechanisms.live_names(self.home / "nope", "skill"), [])

    def test_live_mcp_reads_toml_server_names(self) -> None:
        (self.home / "config.toml").write_text(
            '[mcp_servers.alpha]\ncommand = "a"\n'
            "[mcp_servers.alpha.tools.x]\nenabled = true\n"
            '[mcp_servers.beta]\ncommand = "b"\n[other]\nk = 1\n'
        , encoding="utf-8")
        names = mechanisms.live_mcp(self.home, "toml")
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
        # a parked plugin the CLI cannot list still gets a (unticked) row
        self.assertIs(by_name["x"].enabled, False)

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

    def test_collect_carries_cost(self) -> None:
        self.write("skills/big/SKILL.md", "---\nname: big\ndescription: " + "d" * 396 + "\n---\nbody")
        rows = {r.name: r for r in picker.collect({"disabled": {}}, build(self.tmp))}
        # name (3) + description (396) = 399 chars -> 100 tokens
        self.assertEqual(rows["big"].tokens, 100)

    def test_visible_filters_and_sorts(self) -> None:
        rows = [picker.Row("claude", "skill", "a", True, 5),
                picker.Row("codex", "skill", "b", True, 50, shared=("opencode",)),
                picker.Row("claude", "agent", "c", True, 20),
                picker.Row("claude", "skill", "d", False, 0, 99)]
        # name order is the input order; cost order is biggest live first, parked last
        self.assertEqual([r.name for r in picker.visible(rows, sort="cost")], ["b", "c", "a", "d"])
        # harness chip matches the owner and the sharers
        self.assertEqual([r.name for r in picker.visible(rows, harness="opencode")], ["b"])
        # type chip and text query combine
        self.assertEqual([r.name for r in picker.visible(rows, "claude", type_="skill")], ["a", "d"])

    def test_cycle_wraps_and_recovers(self) -> None:
        self.assertEqual(picker.cycle(("name", "cost"), "name"), "cost")
        self.assertEqual(picker.cycle(("name", "cost"), "cost"), "name")
        self.assertEqual(picker.cycle(["all", "x"], "gone"), "all")

    def test_cost_cell_shows_saving_for_parked(self) -> None:
        self.assertEqual(picker.Row("claude", "skill", "a", True, 12).cost_cell, "12")
        self.assertEqual(picker.Row("claude", "skill", "a", False, 0, 7).cost_cell, "(7)")


class FakeWin:
    """Just enough of a curses window: records addnstr calls, replays queued keys."""

    def __init__(self, keys=(), size=(8, 60)):
        self.size, self.keys, self.calls = size, list(keys), []

    def getmaxyx(self):
        return self.size

    def addnstr(self, y, x, text, n, attr=0):
        self.calls.append((y, x, text[:n], attr))

    def get_wch(self):
        return self.keys.pop(0)

    def erase(self): pass
    def noutrefresh(self): pass
    def keypad(self, flag): pass


@unittest.skipIf(picker is None, "curses unavailable")
class PickerColorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = [picker.Row("claude", "skill", "a", True, 5),
                     picker.Row("codex", "skill", "b", False, 0, 7)]
        self.rows[1].staged = True            # pending enable
        p = mock.patch.multiple(
            picker.curses, doupdate=mock.DEFAULT, curs_set=mock.DEFAULT,
            start_color=mock.DEFAULT, use_default_colors=mock.DEFAULT,
            init_pair=mock.DEFAULT, create=True)
        self.m = p.start()
        self.addCleanup(p.stop)
        cp = mock.patch.object(picker.curses, "color_pair", side_effect=lambda n: n << 8)
        cp.start()
        self.addCleanup(cp.stop)

    def test_state_pair(self) -> None:
        self.assertEqual(picker.state_pair(self.rows[0]), picker.P_LIVE)
        self.assertEqual(picker.state_pair(self.rows[1]), picker.P_PENDING)
        self.rows[1].staged = False
        self.assertEqual(picker.state_pair(self.rows[1]), picker.P_PARKED)

    def test_monochrome_draw_is_one_call_per_row_with_todays_attrs(self) -> None:
        win = FakeWin()
        picker.draw(win, self.rows, "", 0, 0, 1, "chips", color=False)
        body = [c for c in win.calls if 1 <= c[0] <= 2]
        self.assertEqual([c[0] for c in body], [1, 2])
        self.assertEqual(body[0][3], picker.curses.A_REVERSE)
        self.assertEqual(body[1][3], picker.curses.A_NORMAL | picker.curses.A_BOLD)
        self.assertTrue(all(len(c[2]) == 59 for c in body))

    def test_color_draw_segments_rebuild_same_text_and_cursor_row_stays_reversed(self) -> None:
        mono, col = FakeWin(), FakeWin()
        picker.draw(mono, self.rows, "", 0, 0, 1, color=False)
        picker.draw(col, self.rows, "", 0, 0, 1, color=True)
        for y in (1, 2):
            text = "".join(c[2] for c in sorted(col.calls, key=lambda c: c[1]) if c[0] == y)
            self.assertEqual(text, next(c[2] for c in mono.calls if c[0] == y))
        cursor = [c for c in col.calls if c[0] == 1]
        self.assertEqual(len(cursor), 1)                       # one reversed bar
        self.assertTrue(cursor[0][3] & picker.curses.A_REVERSE)
        self.assertEqual((cursor[0][3] >> 8) & 0xFF, picker.P_LIVE)
        other = {c[1]: (c[3] >> 8) & 0xFF for c in col.calls if c[0] == 2}
        self.assertEqual(sorted(other.values()),
                         [0, picker.P_HARNESS, picker.P_TYPE, picker.P_PENDING])

    def test_loop_without_has_colors_never_touches_pairs(self) -> None:
        with mock.patch.object(picker.curses, "has_colors", return_value=False):
            out = picker.loop(FakeWin(["\t", "\n"]), self.rows, color=True)
        self.assertEqual([r.name for r in out], ["a", "b"])
        self.m["start_color"].assert_not_called()
        self.m["init_pair"].assert_not_called()

    def test_loop_with_colors_inits_five_pairs_and_color_off_skips(self) -> None:
        with mock.patch.object(picker.curses, "has_colors", return_value=True):
            self.assertIsNone(picker.loop(FakeWin(["\x1b"]), self.rows, color=True))
            self.assertEqual(self.m["init_pair"].call_count, 5)
            self.m["init_pair"].reset_mock()
            self.assertIsNone(picker.loop(FakeWin(["\x1b"]), self.rows, color=False))
            self.m["init_pair"].assert_not_called()
