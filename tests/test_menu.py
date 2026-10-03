"""Numbered-menu fallback (DESIGN s5.10): scripted stdin, no curses, never blocks."""
from __future__ import annotations

import contextlib
import io
import sys
import unittest
from unittest import mock

from base import SandboxCase

import agent_toggle.ui as ui_pkg
from agent_toggle import cli, store
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

    def test_toggle_by_number_then_apply(self) -> None:
        changes, out = self.run_menu("1\n3\na\n")
        self.assertEqual([(r.name, r.staged) for r in changes], [("alpha", False), ("gamma", False)])
        self.assertIn("[x]", out)
        self.assertIn("2 staged", out)

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


if __name__ == "__main__":
    unittest.main()
