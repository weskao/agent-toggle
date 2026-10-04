"""Numbered-menu fallback (DESIGN s5.10): scripted stdin, no curses, never blocks."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from unittest import mock

from base import SandboxCase
from test_project import ProjectCase

import agent_toggle.ui as ui_pkg
from agent_toggle import cli, fs, settings, store
from agent_toggle.harnesses import build
from agent_toggle.ui import menu


class MenuTest(SandboxCase):
    def setUp(self) -> None:
        super().setUp()
        for n in ("alpha", "beta", "gamma"):
            self.write(f"skills/{n}/SKILL.md")
        self.table = {"claude": build(self.tmp)["claude"]}

    def run_menu(self, script: str):
        out = io.StringIO()
        changes = menu.pick({"version": 3, "disabled": {}}, self.table, plugins=False,
                            stdin=io.StringIO(script), stdout=out)
        return changes, out.getvalue()

    def test_zh_tw_heading_on_an_ascii_pipe_never_crashes(self) -> None:
        from agent_toggle import i18n
        self.addCleanup(i18n.set_language, "en")
        i18n.set_language("zh-TW")
        raw = io.BytesIO()
        out = io.TextIOWrapper(raw, encoding="ascii")
        menu.pick({"version": 3, "disabled": {}}, self.table, plugins=False,
                  stdin=io.StringIO("q\n"), stdout=out)
        out.flush()
        self.assertIn(b"alpha", raw.getvalue())

    def test_a_non_ascii_row_on_an_ascii_pipe_never_crashes(self) -> None:
        self.write("skills/技能/SKILL.md")                      # the row text goes via encodable
        out = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
        menu.pick({"version": 3, "disabled": {}}, self.table, plugins=False,
                  stdin=io.StringIO("q\n"), stdout=out)
        out.flush()
        self.assertIn(b"alpha", out.buffer.getvalue())
        self.assertIn(b"?", out.buffer.getvalue())

    def test_an_explicit_harness_is_shown_even_when_off_in_settings(self) -> None:
        settings.set("harness.claude", False)
        _, out = self.run_menu("q\n")
        self.assertNotIn("alpha", out)
        changes = menu.pick({"version": 3, "disabled": {}}, self.table, plugins=False,
                            stdin=io.StringIO("1\na\n"), stdout=io.StringIO(), harness="claude")
        self.assertEqual([r.name for r in changes], ["alpha"])
        with mock.patch.dict(sys.modules, {"agent_toggle.ui.picker": None}), \
                mock.patch.object(menu, "pick", return_value=None) as pick, \
                contextlib.redirect_stdout(io.StringIO()):
            saved = ui_pkg.__dict__.pop("picker", None)
            if saved is not None:
                self.addCleanup(setattr, ui_pkg, "picker", saved)
            self.assertEqual(cli.main(["ui", "--harness", "claude"]), 0)
        self.assertEqual(pick.call_args.kwargs["harness"], "claude")

    def test_toggle_by_number_then_apply(self) -> None:
        changes, out = self.run_menu("1\n3\na\n")
        self.assertEqual([(r.name, r.staged) for r in changes], [("alpha", False), ("gamma", False)])
        self.assertIn("Skills (3)", out)                       # grouped under a type heading
        self.assertIn("  1  *         2  claude   alpha", out)   # ASCII glyphs: not UTF-8
        self.assertIn("  1  o -       2  claude   alpha", out)   # staged to park
        self.assertIn("2 staged", out)
        self.assertNotIn("\x1b[", out)                         # no colour unless asked

    def test_rows_are_numbered_down_the_grouped_screen(self) -> None:
        self.write("agents/zed.md")
        changes, out = self.run_menu("4\na\n")                 # skills 1-3, then agents
        self.assertEqual([r.name for r in changes], ["zed"])
        self.assertLess(out.index("Skills (3)"), out.index("Agents (1)"))

    def test_color_and_unicode_glyphs(self) -> None:
        class Tty(io.StringIO):
            encoding = "utf-8"
        out = Tty()
        menu.pick({"version": 3, "disabled": {}}, self.table, plugins=False, color=True,
                  stdin=io.StringIO("1\nq\n"), stdout=out)
        text = out.getvalue()
        self.assertIn("\x1b[32m●\x1b[0m", text)                # green live glyph
        self.assertIn("\x1b[33m○\x1b[0m", text)                # yellow parked (staged) glyph
        self.assertIn("\x1b[35m-\x1b[0m", text)                # magenta change marker

    def test_settings_hide_harnesses_and_seed_the_view(self) -> None:
        table = build(self.tmp)
        (self.tmp / ".codex" / "skills" / "cx").mkdir(parents=True)
        (self.tmp / ".codex" / "skills" / "cx" / "SKILL.md").write_text("x", encoding="utf-8")
        out = io.StringIO()
        menu.pick({"version": 3, "disabled": {}}, table, plugins=False,
                  stdin=io.StringIO("q\n"), stdout=out)
        self.assertIn("cx", out.getvalue())
        settings.set("harness.codex", False)
        settings.set("picker_sort", "cost")
        out = io.StringIO()
        menu.pick({"version": 3, "disabled": {}}, table, plugins=False,
                  stdin=io.StringIO("q\n"), stdout=out)
        self.assertNotIn("cx", out.getvalue())
        self.assertIn("sort:cost", out.getvalue())

    def test_ranges_lists_and_double_toggle(self) -> None:
        changes, _ = self.run_menu("1-3\n2\na\n")
        self.assertEqual([r.name for r in changes], ["alpha", "gamma"])
        changes, _ = self.run_menu("1,2\n1\na\n")
        self.assertEqual([r.name for r in changes], ["beta"])

    def test_filter_renumbers_the_visible_rows(self) -> None:
        changes, _ = self.run_menu("/gam\n1\na\n")
        self.assertEqual([r.name for r in changes], ["gamma"])

    def test_cancel_and_eof_change_nothing(self) -> None:
        self.assertIsNone(self.run_menu("1\nq\n")[0])
        self.assertIsNone(self.run_menu("1\n")[0])          # EOF without apply
        self.assertIsNone(self.run_menu("")[0])

    def test_apply_with_nothing_staged_is_an_empty_list(self) -> None:
        self.assertEqual(self.run_menu("a\n")[0], [])

    def test_bad_input_is_reported_and_does_not_toggle(self) -> None:
        changes, out = self.run_menu("9\nzzz\n0\na\n")
        self.assertEqual(changes, [])
        self.assertEqual(out.count("not understood"), 3)

    def test_filter_cue_and_hint(self) -> None:
        _, out = self.run_menu("/gam\n/\na\n")
        self.assertIn("filter:/gam", out)                     # the active filter is shown with its `/`
        self.assertIn("filter:- ", out)                       # a bare `/` cleared it
        self.assertIn("/ alone clears", out)
        self.assertIn("p profile", out)

    def test_profile_key_lists_saves_and_stages(self) -> None:
        _, out = self.run_menu("p\nq\n")
        self.assertIn("no profiles saved", out)
        _, out = self.run_menu("p save work\nq\n")
        self.assertIn("saved profile work: 3 items", out)
        doc = json.loads((fs.profiles_dir() / "work.json").read_text(encoding="utf-8"))
        self.assertEqual([i["live"] for i in doc["items"]], [True, True, True])
        # a stored profile that parks beta: `p work` (or `p 1`, `p apply work`) stages it
        doc["items"][1]["live"] = False
        (fs.profiles_dir() / "work.json").write_text(json.dumps(doc), encoding="utf-8")
        for line in ("p work", "p 1", "p apply work"):
            changes, out = self.run_menu(f"{line}\na\n")
            self.assertEqual([(r.name, r.staged) for r in changes], [("beta", False)], line)
            self.assertIn("profile work: 1 staged", out)
        changes, out = self.run_menu("p\nq\n")
        self.assertIn("profiles: 1) work", out)

    def test_profile_key_errors_are_the_cli_messages(self) -> None:
        changes, out = self.run_menu("p save bad:name\np ghost\np save\na\n")
        self.assertEqual(changes, [])
        self.assertIn("error: invalid profile name", out)
        self.assertIn("error: cannot read profile 'ghost'", out)
        self.assertIn("profile: <number|name>", out)
        self.assertFalse(fs.profiles_dir().exists())

    def test_profile_save_is_off_in_a_dry_run(self) -> None:
        out = io.StringIO()
        menu.pick({"version": 3, "disabled": {}}, self.table, plugins=False, dry_run=True,
                  stdin=io.StringIO("p save work\nq\n"), stdout=out)
        self.assertIn("--dry-run", out.getvalue())
        self.assertFalse(fs.profiles_dir().exists())

    def test_sort_and_chips_cycle_without_crashing(self) -> None:
        changes, out = self.run_menu("s\nh\nt\nh\nt\na\n")
        self.assertEqual(changes, [])
        self.assertIn("sort:cost", out)

    def test_nothing_to_show(self) -> None:
        out = io.StringIO()
        res = menu.pick({"version": 3, "disabled": {}}, {"claude": build(self.tmp / "empty")["claude"]},
                        plugins=False, stdin=io.StringIO("a\n"), stdout=out)
        self.assertIsNone(res)
        self.assertIn("nothing to show", out.getvalue())

    def test_cmd_ui_falls_back_to_the_menu_when_curses_is_missing(self) -> None:
        saved = ui_pkg.__dict__.pop("picker", None)          # `from .ui import picker` must fail
        if saved is not None:
            self.addCleanup(setattr, ui_pkg, "picker", saved)
        with mock.patch.dict(sys.modules, {"agent_toggle.ui.picker": None}), \
                mock.patch.object(sys, "stdin", io.StringIO("1\na\n")), \
                contextlib.redirect_stdout(io.StringIO()):
            rc = cli.main(["ui"])
        self.assertEqual(rc, 0)
        self.assertFalse((self.home / "skills" / "alpha").exists())
        self.assertIn("claude:skill:alpha", store.load_state()["disabled"])


class MenuProjectTest(ProjectCase):
    """G8 end to end through the real cmd_ui (menu fallback): project scope in, project scope out."""

    def run_ui(self, script: str, *extra: str):
        saved = ui_pkg.__dict__.pop("picker", None)
        if saved is not None:
            self.addCleanup(setattr, ui_pkg, "picker", saved)
        with mock.patch.dict(sys.modules, {"agent_toggle.ui.picker": None}), \
                mock.patch.object(sys, "stdin", io.StringIO(script)):
            return self.run_cli("ui", "--project", str(self.proj), *extra)

    def test_ui_project_lists_only_the_project_and_toggles_in_project_scope(self) -> None:
        self.write("skills/user-only/SKILL.md")
        rc, out, _ = self.run_ui("2\na\n")           # 1 skill demo-skill, 2 agent demo-agent
        self.assertEqual(rc, 0, out)
        self.assertNotIn("user-only", out)
        self.assertFalse((self.pclaude / "agents" / "demo-agent.md").exists())
        self.assertTrue((self.home / "skills" / "demo-skill" / "SKILL.md").is_file())  # user copy untouched
        key = store.make_key("claude", "agent", "demo-agent", project=self.proj)
        self.assertIn(key, self.state())

    def test_ui_project_dry_run_changes_nothing(self) -> None:
        rc, out, _ = self.run_ui("1\na\n", "--dry-run")
        self.assertEqual(rc, 0, out)
        self.assertIn("would disable", out)
        self.assertTrue((self.pclaude / "agents" / "demo-agent.md").is_file())
        self.assertEqual(self.state(), {})

    def test_ui_project_profiles_are_project_scope(self) -> None:
        rc, out, _ = self.run_ui("p save team\nq\n")
        self.assertEqual(rc, 0, out)
        doc = json.loads((fs.profiles_dir() / "team.json").read_text(encoding="utf-8"))
        self.assertEqual(doc["scope"], "project")
        self.assertEqual({i["name"] for i in doc["items"]}, {"demo-agent", "demo-skill"})
        # a user-scope profile is refused in project scope, like `profile apply`
        self.assertEqual(self.run_cli("profile", "save", "mine")[0], 0)
        _, out, _ = self.run_ui("p mine\nq\n")
        self.assertIn("error: profile 'mine' is user-scope: drop --project", out)

    def test_ui_project_errors_match_disable_project(self) -> None:
        self.assertEqual(self.run_cli("ui", "--project", str(self.tmp / "nope"))[0], 4)
        self.assertEqual(self.run_cli("ui", "--project", str(self.proj), "--harness", "codex")[0], 4)


if __name__ == "__main__":
    unittest.main()
