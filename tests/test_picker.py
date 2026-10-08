"""Picker: data gathering and filtering (`ui.model`, no curses), then the curses screen
driven through a fake window that keeps the painted text.

The data tests use `ui.model`, so they run where curses does not exist (Windows). The
screen tests assert on what is RENDERED (text per screen row), never on call counts."""
from __future__ import annotations

import json
import unittest
from unittest import mock

from base import SandboxCase

from agent_toggle import fs, i18n, mechanisms, profiles, settings
from agent_toggle.harnesses import build
from agent_toggle.output import CliError, Result
from agent_toggle.ui import model, theme

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

    def test_collect_fills_the_detail_facts(self) -> None:
        self.write("skills/live-one/SKILL.md")
        state = {"version": 3, "disabled": {"claude:skill:parked-one": {
            "harness": "claude", "type": "skill", "name": "parked-one", "mechanism": "move",
            "at": "2026-09-19T10:00:00+0000", "parked_at": "/parked/parked-one"}}}
        by = {r.name: r for r in model.collect(state, {"claude": build(self.tmp)["claude"]})}
        live, parked = by["live-one"], by["parked-one"]
        self.assertEqual(model.short_path(live.path), "~/.claude/skills/live-one")
        self.assertEqual((live.since, live.mechanism), ("", "move"))
        self.assertEqual((parked.path, parked.since, parked.mechanism),
                         ("/parked/parked-one", "2026-09-19T10:00:00+0000", "move"))

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

    def test_collect_carries_the_frontmatter_description(self) -> None:
        self.write("skills/solo/SKILL.md",
                   "---\nname: solo\ndescription: park a skill without deleting it\n---\nbody\n")
        self.write("commands/go.md", "---\ndescription: run the go helper\n---\n")
        self.write("agents/helper.md", "---\nname: helper\ndescription: >\n  one two\n  three\n---\n")
        self.write("skills/bare/SKILL.md", "# no frontmatter\n")
        self.write("rules/note.md", "---\ndescription: always on note\n---\nbody\n")
        park = self.tmp / "parked-skill"
        park.mkdir()
        (park / "SKILL.md").write_text(
            "---\ndescription: parked copy of the skill\n---\n", encoding="utf-8")
        state = {"disabled": {"claude:skill:parked-skill": {
            "harness": "claude", "type": "skill", "name": "parked-skill",
            "parked_at": str(park)}}}
        rows = {r.name: r for r in model.collect(state, build(self.tmp))}
        self.assertEqual(rows["solo"].description, "park a skill without deleting it")
        self.assertEqual(rows["go"].description, "run the go helper")
        self.assertEqual(rows["helper"].description, "one two three")
        self.assertEqual(rows["bare"].description, "")
        self.assertEqual(rows["note"].description, "always on note")
        self.assertEqual(rows["parked-skill"].description, "parked copy of the skill")

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

    def test_grouped_keeps_the_type_order_and_the_sort_inside(self) -> None:
        rows = [model.Row("claude", "mcp", "m", True), model.Row("claude", "skill", "b", True),
                model.Row("claude", "agent", "c", True), model.Row("claude", "skill", "a", True)]
        self.assertEqual([(ty, [r.name for r in g]) for ty, g in model.grouped(rows)],
                         [("skill", ["b", "a"]), ("agent", ["c"]), ("mcp", ["m"])])

    def test_prefs_hide_switched_off_harnesses_and_seed_the_start_view(self) -> None:
        rows = [model.Row("claude", "skill", "a", True),
                model.Row("codex", "skill", "b", True),
                model.Row("codex", "agent", "c", True, shared=("claude",))]
        self.assertEqual(model.prefs(rows), (rows, ["claude", "codex"], "all", "all", "name"))
        settings.set("harness.codex", False)
        settings.set("picker_sort", "cost")
        settings.set("picker_type", "agent")
        kept, names, harness, type_, sort = model.prefs(rows)
        # a codex-only row is hidden; one shared with an enabled harness stays
        self.assertEqual([r.name for r in kept], ["a", "c"])
        self.assertEqual((names, harness, type_, sort), (["claude"], "all", "agent", "cost"))

    def test_cycle_wraps_and_recovers(self) -> None:
        self.assertEqual(model.cycle(("name", "cost"), "name"), "cost")
        self.assertEqual(model.cycle(("name", "cost"), "cost"), "name")
        self.assertEqual(model.cycle(["all", "x"], "gone"), "all")

    def test_cost_cell_and_token_format(self) -> None:
        self.assertEqual(model.Row("claude", "skill", "a", True, 12).cost_cell, "12")
        self.assertEqual(model.Row("claude", "skill", "a", False, 0, 7).cost_cell, "(7)")
        self.assertEqual([model.fmt_tokens(n) for n in (0, 999, 1000, 12345)],
                         ["0", "999", "1.0k", "12.3k"])


class FakeWin:
    """A curses window stand-in: keeps the painted screen as text (one string per row,
    trailing blanks stripped) and replays queued keys. A `(h, w)` tuple in the key queue
    resizes the window and returns KEY_RESIZE; an exception instance is raised."""

    def __init__(self, keys=(), size=(24, 80)):
        self.size, self.keys, self.frames = size, list(keys), []
        self.erase()

    def getmaxyx(self):
        return self.size

    def erase(self):
        h, w = self.size
        self.grid = [[" "] * w for _ in range(h)]
        self.attrs: dict[tuple[int, int], int] = {}

    def addstr(self, y, x, text, attr=0):
        h, w = self.size
        if not 0 <= y < h or x < 0 or x + len(text) > w:
            raise picker.curses.error("out of window")
        for i, ch in enumerate(text):
            self.grid[y][x + i] = ch
            self.attrs[y, x + i] = attr

    def refresh(self):
        self.frames.append(["".join(r).rstrip() for r in self.grid])

    def keypad(self, flag):
        pass

    def get_wch(self):
        key = self.keys.pop(0)
        if isinstance(key, tuple):
            self.size = key
            return picker.curses.KEY_RESIZE
        if isinstance(key, BaseException):
            raise key
        return key

    @property
    def screen(self) -> list[str]:
        return self.frames[-1]

    def text(self, frame: int = -1) -> str:
        return "\n".join(self.frames[frame])

    def find(self, needle: str) -> int:
        """The screen row (last frame) holding `needle`."""
        return next(y for y, line in enumerate(self.screen) if needle in line)


def demo_rows() -> list:
    """collect() order (harness, type, name). On screen, grouped by type:
    Skills alpha beta zeta, Agents gamma, Commands delta."""
    return [model.Row("claude", "agent", "gamma", True, 50, shared=("codex",)),
            model.Row("claude", "skill", "alpha", True, 1200, path="/x/skills/alpha"),
            model.Row("claude", "skill", "beta", False, 0, 300, path="/x/skills-disabled/beta",
                      since="2026-09-19T10:00:00+0000", mechanism="move"),
            model.Row("claude", "skill", "zeta", True, 5000),
            model.Row("codex", "command", "delta", True, 10)]


@unittest.skipIf(picker is None, "curses unavailable")
class ScreenCase(SandboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.rows = demo_rows()

    def run_keys(self, keys, size=(24, 80), color=False, glyphs=theme.UNICODE, rows=None,
                 dry_run=False):
        win = FakeWin(keys, size)
        out = picker.loop(win, self.rows if rows is None else rows, color=color,
                          dry_run=dry_run, glyphs=glyphs)
        return out, win

    def names(self, out) -> list[tuple[str, bool]]:
        return [(r.name, r.staged) for r in out]


class KeysTest(ScreenCase):
    def test_space_stays_on_the_row_and_tab_advances(self) -> None:
        out, _ = self.run_keys([" ", " ", " ", "\n"])             # 3 presses, all on alpha
        self.assertEqual(self.names(out), [("alpha", False)])
        self.rows = demo_rows()
        out, win = self.run_keys(["\t", " ", "\n"])               # Tab: alpha, then beta
        self.assertEqual(self.names(out), [("alpha", False), ("beta", True)])
        # the staged rows carry a - / + marker and their glyph flips
        line = win.screen[win.find("alpha")]
        self.assertIn("○ - alpha", line)
        self.assertIn("● + beta", win.screen[win.find("beta")])

    def test_toggling_twice_unstages(self) -> None:
        out, _ = self.run_keys(["\t", "\x10", " ", "\n"])         # Tab down, Ctrl-P back up
        self.assertEqual(out, [])

    def test_enter_applies_and_esc_or_ctrl_c_cancel(self) -> None:
        self.assertEqual(self.run_keys(["\n"])[0], [])
        self.assertIsNone(self.run_keys([" ", "\x1b"])[0])
        self.assertIsNone(self.run_keys([" ", "\x03"])[0])
        self.assertIsNone(self.run_keys([" ", KeyboardInterrupt()])[0])

    def test_moving(self) -> None:
        C = picker.curses
        cases = {(C.KEY_END,): "delta", (C.KEY_END, C.KEY_HOME): "alpha",
                 (C.KEY_DOWN, C.KEY_DOWN): "zeta", ("\x0e", C.KEY_UP): "alpha",
                 (C.KEY_NPAGE,): "delta", (C.KEY_NPAGE, C.KEY_PPAGE): "alpha"}
        for keys, name in cases.items():
            self.rows = demo_rows()
            out, _ = self.run_keys([*keys, " ", "\n"])
            self.assertEqual([r.name for r in out], [name], keys)

    def test_typing_filters_and_shows_the_input_with_a_cursor(self) -> None:
        out, win = self.run_keys(["/", "g", "a", "\t", "\n"])
        self.assertEqual(self.names(out), [("gamma", False)])
        typed = win.frames[3]                                 # after `/`, `g`, `a`
        self.assertIn("ga█", typed[2])
        self.assertIn(theme.UNICODE.search, typed[2])    # the search icon marks the bar
        self.assertIn("gamma", "\n".join(typed))
        self.assertNotIn("alpha", "\n".join(typed[3:-2]))
        # idle, the filter line is a placeholder, not an input
        self.assertIn("Type to search", win.frames[0][2])
        self.assertNotIn("█", win.frames[0][2])

    def test_command_keys_only_act_with_no_filter(self) -> None:
        # with no filter, `s` sorts ...
        _, win = self.run_keys(["s", "\x1b"])
        self.assertIn("sort: cost", win.screen[2])
        # ... after `/` it is a letter, and so is every letter after an implicit start
        _, win = self.run_keys(["/", "s", "\x1b"])
        self.assertIn(" s█", win.screen[2])
        self.assertIn("sort: name", win.screen[2])
        _, win = self.run_keys(["x", "s", "\x1b"])
        self.assertIn(" xs█", win.screen[2])
        # Space types while filtering (it separates AND terms) instead of toggling
        out, _ = self.run_keys(["/", "l", " ", "\n"])
        self.assertEqual(out, [])
        # Backspace emptying the filter returns to command mode
        _, win = self.run_keys(["/", "x", "\x7f", "s", "\x1b"])
        self.assertIn("sort: cost", win.screen[2])
        self.assertIn("Type to search", win.screen[2])
        # Ctrl-U clears a filter outright
        _, win = self.run_keys(["/", "z", "z", "\x15", "\x1b"])
        self.assertIn("alpha", win.text())

    def test_search_is_fuzzy_when_nothing_matches_and_also_reads_the_path(self) -> None:
        # `alp` is plain text in `alpha`; `apa` is only a letters-in-order match
        out, win = self.run_keys(["/", "a", "p", "a", "\t", "\n"])
        self.assertEqual([r.name for r in out], ["alpha"])
        self.assertIn("fuzzy match", win.frames[-2][2])
        # a path-only term (`skills-disabled` is beta's parked path) finds beta
        out, _ = self.run_keys(["/", *"skills-disabled", "\t", "\n"], rows=demo_rows())
        self.assertEqual([r.name for r in out], ["beta"])
        # a plain hit is never labelled fuzzy
        _, win = self.run_keys(["/", "a", "l", "\x1b"])
        self.assertNotIn("fuzzy match", win.screen[2])

    def test_emoji_search_icon_only_where_it_renders(self) -> None:
        self.assertTrue(theme.emoji_ok({"TERM": "xterm-256color"}, "darwin"))
        self.assertFalse(theme.emoji_ok({"TERM": "linux"}, "linux"))
        self.assertFalse(theme.emoji_ok({}, "win32"))
        self.assertTrue(theme.emoji_ok({"WT_SESSION": "x"}, "win32"))
        self.assertEqual(theme.ASCII.search, "/")

    def test_tabs_switch_with_arrows_and_digits(self) -> None:
        C = picker.curses
        _, win = self.run_keys(["\x1b"])
        # All counts everything; codex counts its own row and the one shared with it
        self.assertIn(" All 5 ", win.screen[1])
        self.assertIn(" claude 4 ", win.screen[1])
        self.assertTrue(win.screen[1].endswith(" codex 2"))
        for keys, shown, hidden in (([C.KEY_RIGHT], "zeta", "delta"),
                                    (["2"], "delta", "zeta"),
                                    ([C.KEY_LEFT], "delta", "zeta"),          # wraps to codex
                                    (["2", "0"], "delta", None),
                                    (["h", "h"], "gamma", "alpha")):
            _, win = self.run_keys([*keys, "\x1b"])
            self.assertIn(shown, win.text(), keys)
            if hidden:
                self.assertNotIn(hidden, win.text(), keys)
        # a digit with no such tab is ignored
        _, win = self.run_keys(["9", "\x1b"])
        self.assertIn("delta", win.text())
        self.assertIn("zeta", win.text())

    def test_type_cycles_and_groups_have_headings(self) -> None:
        _, win = self.run_keys(["\x1b"])
        heads = [line.split()[0] for line in win.screen[3:] if "──" in line]
        self.assertEqual(heads, ["Skills", "Agents", "Commands"])
        self.assertIn("Skills 3", win.text())
        _, win = self.run_keys(["t", "\x1b"])
        self.assertIn("type: Skills", win.screen[2])
        self.assertNotIn("gamma", win.text())
        out, win = self.run_keys(["t", "t", " ", "\n"])
        self.assertEqual([r.name for r in out], ["gamma"])

    def test_sort_by_cost_holds_inside_each_group(self) -> None:
        out, win = self.run_keys(["s", " ", "\n"])
        self.assertEqual([r.name for r in out], ["zeta"])
        self.assertLess(win.find("zeta"), win.find("alpha"))
        self.assertLess(win.find("alpha"), win.find("beta"))

    def test_toggle_all_visible(self) -> None:
        out, _ = self.run_keys(["a", "\n"])                 # not all live -> all live
        self.assertEqual(self.names(out), [("beta", True)])
        out, _ = self.run_keys(["a", "\n"])                 # all live now -> all parked
        self.assertEqual(sorted(r.name for r in out), ["alpha", "delta", "gamma", "zeta"])
        for r in self.rows:
            r.staged = r.enabled
        out, _ = self.run_keys(["/", "z", "\x01", "\n"])    # Ctrl-A works while filtering
        self.assertEqual(self.names(out), [("zeta", False)])

    def test_help_overlay_opens_and_any_key_closes_it(self) -> None:
        _, win = self.run_keys(["?", "x", "\x1b"])
        overlay = win.text(1)
        self.assertIn("Keys", overlay)
        self.assertIn("toggle every visible row", overlay)
        self.assertIn("╭", overlay)
        self.assertNotIn("toggle every visible row", win.text())
        self.assertIn("Type to search", win.screen[2])             # the closing key was not typed
        self.assertIn("p", [k for k, _ in picker.help_lines(theme.UNICODE)])

    def test_long_list_scrolls_with_the_cursor_and_its_heading(self) -> None:
        rows = [model.Row("claude", "skill", f"s{n:02}", True) for n in range(30)]
        rows.append(model.Row("claude", "agent", "last", True))
        C = picker.curses
        _, win = self.run_keys([C.KEY_NPAGE, "\x1b"], rows=rows, size=(12, 80))
        cursor = next(line for line in win.screen if line.startswith("›"))
        self.assertIn("s06", cursor)                         # 6-line body: one page down
        self.assertNotIn("s00", win.text())
        out, win = self.run_keys([C.KEY_END, " ", "\n"], rows=rows, size=(12, 80))
        self.assertEqual([r.name for r in out], ["last"])
        self.assertIn("Agents 1", win.text(-2))              # the heading scrolled in with it

    def test_space_on_an_empty_list_does_not_start_a_filter(self) -> None:
        out, win = self.run_keys(["2", "t", " ", "s", "\n"])      # codex tab + Skills: empty
        self.assertEqual(out, [])
        self.assertIn("Type to search", win.screen[2])
        self.assertIn("sort: cost", win.screen[2])                # `s` still a command

    def test_the_active_tab_always_fits(self) -> None:
        rows = [model.Row(n, "skill", "x", True) for n in
                ("claude", "codex", "grok", "opencode", "openclaw", "copilot", "vibe")]
        _, win = self.run_keys(["7", "\x1b"], rows=rows, size=(24, 40))
        self.assertIn("vibe 1", win.screen[1])
        _, win = self.run_keys(["\x1b"], rows=rows, size=(24, 40))
        self.assertIn("All 7", win.screen[1])

    def test_a_tiny_window_keeps_status_and_keys(self) -> None:
        out, win = self.run_keys([" ", "\n"], size=(5, 40))
        self.assertEqual([r.name for r in out], ["alpha"])
        self.assertIn("of 5 shown", win.frames[0][-3])
        self.assertIn("Space toggle", win.frames[0][-1])

    def test_resize_redraws_at_the_new_size(self) -> None:
        _, win = self.run_keys([(30, 120), (12, 50), (4, 30), "\x1b"])
        self.assertEqual(len(win.frames[1]), 30)
        self.assertIn("│", "".join(line[60:] for line in win.frames[1][3:]))   # detail pane
        self.assertEqual(len(win.frames[2]), 12)
        self.assertTrue(all(len(line) <= 50 for line in win.frames[2]))
        self.assertEqual(len(win.screen), 4)


class LayoutTest(ScreenCase):
    def test_title_bar_summary(self) -> None:
        _, win = self.run_keys([" ", "\x1b"])
        self.assertIn("agent-toggle  v", win.frames[0][0])
        self.assertIn("5 resources · ~6.3k tok live", win.frames[0][0])
        self.assertIn("1 staged (-1.2k tok)", win.screen[0])

    def test_status_line_says_ctrl_c_leaves_and_auto_saves(self) -> None:
        _, win = self.run_keys(["\x1b"])
        self.assertEqual(win.screen[-3].strip(), "5 of 5 shown · 1/5")
        self.assertEqual(win.screen[-2].strip(), "Ctrl+C to leave · auto-save")

    def test_detail_pane_shows_the_description(self) -> None:
        text = "park a skill without deleting it"
        rows = [model.Row("claude", "skill", "solo", True, description=text)]
        _, win = self.run_keys(["\x1b"], rows=rows, size=(24, 120))
        pane = "\n".join(line[72:] for line in win.screen)
        self.assertIn("description", pane)
        self.assertIn(text, pane)
        bare = [model.Row("claude", "mcp", "srv", True)]
        _, win = self.run_keys(["\x1b"], rows=bare, size=(24, 120))
        self.assertNotIn("description", "\n".join(line[72:] for line in win.screen))

    def test_detail_pane_wraps_the_description_on_words(self) -> None:
        r = model.Row("claude", "skill", "solo", True, description="alpha beta gamma delta")
        lines = picker.detail_lines(r, 23, theme.ASCII)
        body = ["".join(s for s, role in line if role == "text").strip() for line in lines]
        self.assertIn("alpha beta", body)
        self.assertIn("gamma delta", body)

    def test_detail_pane_description_is_translated(self) -> None:
        self.addCleanup(i18n.set_language, i18n.LANGUAGE)
        i18n.set_language("zh-TW")
        rows = [model.Row("claude", "skill", "solo", True, description="park a skill")]
        _, win = self.run_keys(["\x1b"], rows=rows, size=(24, 120))
        pane = "\n".join(line[72:] for line in win.screen)
        self.assertIn("說明", pane)
        self.assertIn("park a skill", pane)

    def test_detail_pane_shows_at_wide_widths_only(self) -> None:
        down = picker.curses.KEY_DOWN
        _, win = self.run_keys([down, "\x1b"], size=(24, 120))
        pane = "\n".join(line[72:] for line in win.screen)
        for text in ("beta", "harness", "claude", "○ parked", "path", "/x/skills-disabled/beta",
                     "since", "2026-09-19 10:00:00", "mechanism", "move", "~300 tok if restored"):
            self.assertIn(text, pane)
        _, win = self.run_keys([" ", picker.curses.KEY_UP, "\x1b"], size=(24, 120))
        self.assertIn("staged → parked", win.text())
        _, win = self.run_keys([down, "\x1b"], size=(24, 80))
        self.assertNotIn("mechanism", win.text())
        self.assertNotIn("│", "\n".join(win.screen[3:-2]))

    def test_narrow_widths_degrade_but_keep_names_and_state(self) -> None:
        for width in (80, 60, 40, 30):
            _, win = self.run_keys(["\x1b"], size=(24, width))
            self.assertTrue(all(len(line) <= width for line in win.screen), width)
            self.assertRegex(win.text(), r"●\s+alpha")
            self.assertIn("Space toggle", win.screen[-1])
        _, win = self.run_keys(["\x1b"], size=(24, 90))
        self.assertIn("+shared", win.text())
        self.assertIn("1.2k", win.screen[win.find("alpha")])

    def test_cost_column_and_parked_rows(self) -> None:
        _, win = self.run_keys(["\x1b"], size=(24, 90))
        self.assertIn("(300)", win.screen[win.find("beta")])
        self.assertIn("○", win.screen[win.find("beta")])

    def test_empty_result_says_how_to_get_back(self) -> None:
        _, win = self.run_keys(["/", "q", "q", "\x1b"])
        self.assertIn("nothing matches", win.text())
        self.assertIn("0 of 5 shown", win.screen[-3])
        # with the detail pane, the message is cut at the list's edge, not drawn under the pane
        _, win = self.run_keys(["/", "q", "q", "\x1b"], size=(24, 120))
        line = win.screen[3]
        self.assertIn("nothing matches", line)
        self.assertTrue(line.rstrip().endswith("│"), line)

    def test_typing_mode_swaps_the_key_chips(self) -> None:
        _, win = self.run_keys(["/", "\x1b"])
        self.assertIn("Space toggle", win.frames[0][-1])
        self.assertIn("Tab toggle", win.screen[-1])
        self.assertIn("clear filter", win.screen[-1])

    def test_dry_run_is_announced(self) -> None:
        _, win = self.run_keys(["\x1b"], dry_run=True)
        self.assertIn("dry run", win.screen[-3])

    def test_mono_draw_uses_attributes_only(self) -> None:
        _, win = self.run_keys(["\x1b"], size=(24, 90))
        C = picker.curses
        y = win.find("alpha")
        self.assertTrue(win.attrs[y, 2] & C.A_BOLD)                  # live glyph: bold
        self.assertTrue(win.attrs[y, 6] & C.A_REVERSE)               # cursor bar
        beta = win.find("beta")
        self.assertTrue(win.attrs[beta, 2] & C.A_DIM)                # parked glyph: dim

    def test_color_draw_uses_pairs_and_no_colors_falls_back(self) -> None:
        C = picker.curses
        with mock.patch.multiple(C, has_colors=mock.DEFAULT, start_color=mock.DEFAULT,
                                 use_default_colors=mock.DEFAULT, init_pair=mock.DEFAULT,
                                 create=True) as m, \
                mock.patch.object(C, "color_pair", side_effect=lambda n: n << 8):
            m["has_colors"].return_value = True
            _, win = self.run_keys(["\x1b"], color=True, size=(24, 90))
            self.assertTrue(m["init_pair"].called)
            y = win.find("alpha")
            self.assertNotEqual((win.attrs[y, 2] >> 8) & 0xFF, 0)          # a coloured glyph
            self.assertNotEqual(win.attrs[y, 2], win.attrs[win.find("beta"), 2])
            m["init_pair"].reset_mock()
            m["has_colors"].return_value = False
            out, win = self.run_keys([" ", "\n"], color=True)
            m["init_pair"].assert_not_called()
            self.assertEqual([r.name for r in out], ["alpha"])
            self.run_keys(["\x1b"], color=False)
            m["init_pair"].assert_not_called()

    def test_ascii_glyphs(self) -> None:
        _, win = self.run_keys(["\x1b"], glyphs=theme.ASCII)
        self.assertIn("*   alpha", win.text())
        self.assertIn("o   beta", win.text())
        self.assertNotIn("●", win.text())
        self.assertIn("Lt/Rt harness", win.screen[-1])

    def test_zh_tw_chrome(self) -> None:
        self.addCleanup(i18n.set_language, i18n.LANGUAGE)
        i18n.set_language("zh-TW")
        _, win = self.run_keys(["\x1b"], size=(24, 100))
        self.assertIn("技能", win.text())
        self.assertIn("全部", win.screen[1])
        self.assertIn("Ctrl+C 離開 · 自動儲存", win.screen[-2])


class SettingsTest(ScreenCase):
    def test_disabled_harness_rows_and_tab_are_hidden(self) -> None:
        settings.set("harness.codex", False)
        _, win = self.run_keys(["\x1b"])
        self.assertNotIn("delta", win.text())
        self.assertIn("gamma", win.text())                  # owned by claude, shared with codex
        self.assertNotIn("codex", win.screen[1])
        self.assertIn("4 resources", win.screen[0])

    def test_an_explicit_harness_shows_its_rows_even_when_off_in_settings(self) -> None:
        settings.set("harness.claude", False)
        _, win = self.run_keys(["\x1b"])
        self.assertNotIn("alpha", win.text())
        win = FakeWin(["\x1b"])
        picker.loop(win, self.rows, harness="claude")         # `ui --harness claude`
        self.assertIn("alpha", win.text())

    def test_saved_sort_harness_and_type_are_the_start_view(self) -> None:
        settings.set("picker_sort", "cost")
        out, _ = self.run_keys([" ", "\n"])
        self.assertEqual([r.name for r in out], ["zeta"])
        settings.set("picker_harness", "codex")
        _, win = self.run_keys(["\x1b"])
        self.assertNotIn("alpha", win.text())
        settings.set("picker_type", "command")
        _, win = self.run_keys(["\x1b"])
        self.assertIn("type: Commands", win.screen[2])
        # a saved value with no rows behind it starts on All
        settings.set("picker_type", "mcp")
        _, win = self.run_keys(["\x1b"])
        self.assertIn("type: All", win.screen[2])

    def test_pick_keeps_its_contract(self) -> None:
        for n in ("one", "two"):
            self.write(f"skills/{n}/SKILL.md")
        table = {"claude": build(self.tmp)["claude"]}
        with mock.patch.object(picker.curses, "wrapper",
                               side_effect=lambda fn, *a: fn(FakeWin([" ", "\n"]), *a)):
            out = picker.pick({"version": 3, "disabled": {}}, table, plugins=False)
        self.assertEqual([(r.label, r.staged) for r in out], [("claude/skill/one", False)])


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

    def test_items_of_a_harness_off_in_settings_count_as_hidden(self) -> None:
        fs.private_dir(fs.profiles_dir())
        (fs.profiles_dir() / "work.json").write_text(json.dumps({"version": 1, "items": [
            {"harness": h, "type": "skill", "name": "alpha", "live": False}
            for h in ("claude", "codex", "grok")]}), encoding="utf-8")
        settings.set("harness.codex", False)
        self.assertEqual(model.profile_command(self.rows, "work"),
                         "profile work: 1 staged, 0 already as profiled, 1 not on this machine, "
                         "1 hidden by settings -- Enter to apply")
        self.rows[0].staged = True
        self.assertIn("2 not on this machine -- ",   # `ui --harness codex`: not hidden
                      model.profile_command(self.rows, "work", harness="codex"))

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
        for odd in ("²", "save a\x00b", "a\x00b", "save a\ud800"):
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
        win = FakeWin(keys, size=(12, 70))
        out = picker.loop(win, self.rows)
        self.assertEqual([r.name for r in out], ["alpha"])
        everything = "\n".join(win.text(i) for i in range(len(win.frames)))
        self.assertIn("profile> work", everything)
        self.assertIn("1) work", everything)
        self.assertIn("profile work: 1 staged", win.screen[-3])
        # Esc at the prompt stages nothing (even a full valid name) and the picker carries on
        self.rows[0].staged = True
        self.assertEqual(picker.loop(FakeWin(["p", *"work", "\x1b", "\n"], size=(12, 70)),
                                     self.rows), [])
        # a 2-row window: the prompt clips instead of raising curses.error
        self.assertEqual(picker.ask_profile(FakeWin(["a", "\n"], size=(2, 40))), "a")
        # ASCII terminals get an ASCII input cursor (a block would not encode)
        win = FakeWin(["a", "\n"], size=(8, 40))
        picker.ask_profile(win, None, theme.ASCII)
        self.assertIn("profile> a_", win.text())
        self.assertNotIn("█", win.text())

    @unittest.skipIf(picker is None, "curses unavailable")
    def test_a_profile_never_stages_a_row_of_a_hidden_harness(self) -> None:
        rows = [*self.rows, model.Row("codex", "skill", "alpha", True)]
        fs.private_dir(fs.profiles_dir())
        (fs.profiles_dir() / "work.json").write_text(json.dumps({"version": 1, "items": [
            {"harness": h, "type": "skill", "name": "alpha", "live": False}
            for h in ("claude", "codex")]}), encoding="utf-8")
        settings.set("harness.codex", False)
        out = picker.loop(FakeWin(["p", *"work", "\n", "\n"], size=(12, 70)), rows)
        self.assertEqual([r.label for r in out], ["claude/skill/alpha"])
        self.assertTrue(rows[-1].staged)                    # the codex row was not touched

    @unittest.skipIf(picker is None, "curses unavailable")
    def test_p_is_a_filter_letter_after_slash(self) -> None:
        win = FakeWin(["/", "p", "\x1b"], size=(12, 70))
        picker.loop(win, self.rows)
        self.assertFalse(any("profile>" in line for f in win.frames for line in f))


if __name__ == "__main__":
    unittest.main()
