"""Picker data gathering and filtering (no curses screen is opened).

The data tests use `ui.model`, so they run where curses does not exist (Windows)."""
from __future__ import annotations

import json
import unittest
from unittest import mock

from base import SandboxCase

from agent_toggle import fs, mechanisms, profiles
from agent_toggle.harnesses import build
from agent_toggle.output import CliError, Result
from agent_toggle.ui import model

try:
    from agent_toggle.ui import picker
except ImportError:              # no curses build (Windows without windows-curses)
    picker = None


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
        rows = model.collect(state, {"claude": build(self.tmp)["claude"]})
        by_name = {r.name: r for r in rows}
        # live item present and ticked
        self.assertIs(by_name["live-one"].enabled, True)
        # parked item present and unticked
        self.assertIs(by_name["parked-one"].enabled, False)
        # a parked plugin the CLI cannot list still gets a (unticked) row
        self.assertIs(by_name["x"].enabled, False)

    def test_match_filters_on_all_terms(self) -> None:
        rows = [model.Row("claude", "command", "orch:batch", True),
                model.Row("codex", "skill", "orch-helper", True),
                model.Row("claude", "skill", "unrelated", True)]
        # single term filters
        self.assertEqual(len(model.match(rows, "orch")), 2)
        # terms are ANDed
        self.assertEqual(len(model.match(rows, "orch claude")), 1)
        # match is case-insensitive
        self.assertEqual(len(model.match(rows, "ORCH")), 2)
        # empty query returns everything
        self.assertEqual(len(model.match(rows, "")), 3)

    def test_row_tracks_staged_change(self) -> None:
        r = model.Row("claude", "skill", "demo", True)
        # unchanged row reports no change
        self.assertFalse(r.changed)
        r.staged = False
        # unticking marks a change
        self.assertTrue(r.changed)
        # label is harness/type/name
        self.assertEqual(r.label, "claude/skill/demo")

    def test_collect_carries_cost(self) -> None:
        self.write("skills/big/SKILL.md", "---\nname: big\ndescription: " + "d" * 396 + "\n---\nbody")
        rows = {r.name: r for r in model.collect({"disabled": {}}, build(self.tmp))}
        # name (3) + description (396) = 399 chars -> 100 tokens
        self.assertEqual(rows["big"].tokens, 100)

    def test_visible_filters_and_sorts(self) -> None:
        rows = [model.Row("claude", "skill", "a", True, 5),
                model.Row("codex", "skill", "b", True, 50, shared=("opencode",)),
                model.Row("claude", "agent", "c", True, 20),
                model.Row("claude", "skill", "d", False, 0, 99)]
        # name order is the input order; cost order is biggest live first, parked last
        self.assertEqual([r.name for r in model.visible(rows, sort="cost")], ["b", "c", "a", "d"])
        # harness chip matches the owner and the sharers
        self.assertEqual([r.name for r in model.visible(rows, harness="opencode")], ["b"])
        # type chip and text query combine
        self.assertEqual([r.name for r in model.visible(rows, "claude", type_="skill")], ["a", "d"])

    def test_cycle_wraps_and_recovers(self) -> None:
        self.assertEqual(model.cycle(("name", "cost"), "name"), "cost")
        self.assertEqual(model.cycle(("name", "cost"), "cost"), "name")
        self.assertEqual(model.cycle(["all", "x"], "gone"), "all")

    def test_cost_cell_shows_saving_for_parked(self) -> None:
        self.assertEqual(model.Row("claude", "skill", "a", True, 12).cost_cell, "12")
        self.assertEqual(model.Row("claude", "skill", "a", False, 0, 7).cost_cell, "(7)")


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
    def refresh(self): pass
    def keypad(self, flag): pass


class TypingCueTest(unittest.TestCase):
    """G3: typing mode is visible as text (a `/` prefix, a cursor block, a hint), never as
    colour alone."""

    def test_header_text_idle_and_typing(self) -> None:
        self.assertIn("press / to type", model.header_text("", False))
        self.assertNotIn("\u2588", model.header_text("", False))
        empty = model.header_text("", True)               # `/` pressed, nothing typed yet
        self.assertIn("filter: /\u2588", empty)
        self.assertIn("Esc quits", empty)
        self.assertIn("filter: /abc\u2588", model.header_text("abc", True))
        # a filter typed without `/` (a non-command first letter) reads the same
        self.assertIn("filter: /abc\u2588", model.header_text("abc", False))

    @unittest.skipIf(picker is None, "curses unavailable")
    def test_draw_shows_the_cue_in_monochrome(self) -> None:
        with mock.patch.object(picker.curses, "doupdate", create=True):
            for typing, query in ((False, ""), (True, ""), (True, "tel")):
                win = FakeWin()
                picker.draw(win, [], query, 0, 0, 0, "chips", color=False, typing=typing)
                y, x, text, attr = win.calls[0]
                self.assertEqual(text.rstrip(), model.header_text(query, typing)[:59].rstrip())
                self.assertEqual(bool(attr & picker.curses.A_REVERSE), typing or bool(query))

    @unittest.skipIf(picker is None, "curses unavailable")
    def test_loop_slash_turns_the_cue_on_and_backspace_off(self) -> None:
        rows = [picker.Row("claude", "skill", "a", True)]
        with mock.patch.multiple(picker.curses, doupdate=mock.DEFAULT, curs_set=mock.DEFAULT,
                                 create=True):
            win = FakeWin(["/", "x", "\x7f", "\x1b"])
            self.assertIsNone(picker.loop(win, rows))
        headers = [c[2].strip() for c in win.calls if c[0] == 0]
        self.assertEqual([h.split("   ")[0] for h in headers],
                         ["filter: (press / to type one, ? for keys)", "filter: /\u2588",
                          "filter: /x\u2588", "filter: (press / to type one, ? for keys)"])


class ProfileKeyTest(SandboxCase):
    """G9: the profile prompt's grammar, on rows, with no curses."""

    def setUp(self) -> None:
        super().setUp()
        for n in ("alpha", "beta"):
            self.write(f"skills/{n}/SKILL.md")
        self.table = {"claude": build(self.tmp)["claude"]}
        self.rows = model.collect({"version": 3, "disabled": {}}, self.table, plugins=False)

    def stored(self, name: str, items: list[tuple[str, bool]], **extra) -> None:
        fs.private_dir(fs.profiles_dir())
        (fs.profiles_dir() / f"{name}.json").write_text(json.dumps({
            "version": 1, "items": [{"harness": "claude", "type": "skill", "name": n, "live": live}
                                    for n, live in items], **extra}), encoding="utf-8")

    def test_listing_and_names(self) -> None:
        self.assertIn("no profiles saved", model.profile_listing())
        self.stored("work", [])
        self.stored("play", [])
        self.assertEqual(model.profile_names(), ["play", "work"])
        self.assertEqual(model.profile_listing(), "profiles: 1) play  2) work")

    def test_apply_stages_by_name_or_number_and_never_writes(self) -> None:
        self.stored("work", [("alpha", False), ("beta", True), ("ghost", False)])
        before = [p.read_bytes() for p in sorted(self.home.rglob("*")) if p.is_file()]
        msg = model.profile_command(self.rows, "work")
        self.assertEqual(msg, "profile work: 1 staged, 1 already as profiled, "
                              "1 not on this machine -- Enter to apply")
        self.assertEqual([(r.name, r.staged) for r in self.rows], [("alpha", False), ("beta", True)])
        self.assertEqual([r.name for r in self.rows if r.changed], ["alpha"])
        self.assertEqual([p.read_bytes() for p in sorted(self.home.rglob("*")) if p.is_file()], before)
        self.rows[0].staged = True                       # numbers and `apply` are the same thing
        self.assertIn("1 staged", model.profile_command(self.rows, "1"))
        self.rows[0].staged = True
        self.assertIn("1 staged", model.profile_command(self.rows, "apply work"))

    def test_apply_overrides_an_earlier_tick_only_for_items_it_mentions(self) -> None:
        self.stored("work", [("alpha", True)])
        self.rows[0].staged, self.rows[1].staged = False, False     # user unticked both
        model.profile_command(self.rows, "work")
        self.assertEqual([r.staged for r in self.rows], [True, False])

    def test_save_writes_the_live_state_through_profiles(self) -> None:
        self.rows[0].staged = False                      # a staged tick is NOT saved
        msg = model.profile_command(self.rows, "save work")
        self.assertIn("saved profile work: 2 items", msg)
        doc = json.loads((fs.profiles_dir() / "work.json").read_text(encoding="utf-8"))
        self.assertEqual([(i["name"], i["live"]) for i in doc["items"]],
                         [("alpha", True), ("beta", True)])
        self.assertNotIn("scope", doc)

    def test_save_in_a_dry_run_writes_nothing(self) -> None:
        self.assertIn("--dry-run", model.profile_command(self.rows, "save work", dry_run=True))
        self.assertFalse(fs.profiles_dir().exists())

    def test_names_are_validated_like_the_cli(self) -> None:
        for bad in ("bad:name", "CON", "a/b", "../x"):
            line = f"save {bad}"
            with self.assertRaises(CliError) as cli_err:
                profiles.cmd_save(bad, None, None, Result(json_mode=True))
            self.assertEqual(model.profile_command(self.rows, line), f"error: {cli_err.exception.msg}")
        self.assertFalse(fs.profiles_dir().exists())
        self.assertTrue(model.profile_command(self.rows, "nope").startswith("error: cannot read profile"))
        self.assertEqual(model.profile_command(self.rows, "save"), model.PROFILE_USAGE)
        self.assertEqual(model.profile_command(self.rows, "  "), "")
        # an OS-level refusal is a message, never a crash of the picker
        self.assertTrue(model.profile_command(self.rows, "save " + "x" * 300).startswith("error: "))
        # typeable-but-odd input is a message too: a unicode digit, a NUL, a lone surrogate
        for odd in ("\u00b2", "save a\x00b", "a\x00b", "save a\ud800"):
            self.assertTrue(model.profile_command(self.rows, odd).startswith("error: "), odd)

    def test_scope_mismatch_is_the_cli_error(self) -> None:
        self.stored("proj", [("alpha", False)], scope="project")
        self.assertEqual(model.profile_command(self.rows, "proj"),
                         "error: profile 'proj' is project-scope: pass --project <dir>")
        self.assertFalse(any(r.changed for r in self.rows))

    @unittest.skipIf(picker is None, "curses unavailable")
    def test_curses_prompt_then_enter_returns_the_staged_rows(self) -> None:
        self.stored("work", [("alpha", False)])
        keys = ["p", "w", "o", "r", "k", "x", "\x7f", "\n", "\n"]       # p, `work`, Enter, Enter
        with mock.patch.multiple(picker.curses, doupdate=mock.DEFAULT, curs_set=mock.DEFAULT,
                                 create=True):
            win = FakeWin(keys, size=(12, 70))
            out = picker.loop(win, self.rows)
        self.assertEqual([r.name for r in out], ["alpha"])
        self.assertTrue(any("profile> work" in c[2] for c in win.calls))
        self.assertTrue(any("1) work" in c[2] for c in win.calls))
        self.assertTrue(any("profile work: 1 staged" in c[2] for c in win.calls))
        # Esc at the prompt stages nothing (even a full valid name) and the picker carries on
        self.rows[0].staged = True
        with mock.patch.multiple(picker.curses, doupdate=mock.DEFAULT, curs_set=mock.DEFAULT,
                                 create=True):
            keys = ["p", *"work", "\x1b", "\n"]
            self.assertEqual(picker.loop(FakeWin(keys, size=(12, 70)), self.rows), [])
        # a 2-row window: the prompt clips instead of raising curses.error
        class Tiny(FakeWin):
            def addnstr(self, y, x, text, n, attr=0):
                if y >= self.size[0]:
                    raise picker.curses.error("out of window")
                super().addnstr(y, x, text, n, attr)
        self.assertEqual(picker.ask_profile(Tiny(["a", "\n"], size=(2, 40))), "a")

    @unittest.skipIf(picker is None, "curses unavailable")
    def test_p_is_a_filter_letter_after_slash_and_is_in_the_help(self) -> None:
        with mock.patch.multiple(picker.curses, doupdate=mock.DEFAULT, curs_set=mock.DEFAULT,
                                 create=True):
            win = FakeWin(["/", "p", "\x1b"], size=(12, 70))
            picker.loop(win, self.rows)
        self.assertFalse(any("profile>" in c[2] for c in win.calls))
        self.assertIn("p profile", picker.HELP)
        self.assertTrue(any("  p " in line for line in picker.LONG_HELP))


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
