"""`n` in the picker (and `n <number>` in the numbered menu): toggle every type with the
current row's name on that row's harness, like `disable all <name>`."""
from __future__ import annotations

import io
import unittest

from base import SandboxCase
from test_picker import FakeWin

from agent_toggle.ui import menu, model, theme

try:
    from agent_toggle.ui import picker
except ImportError:              # no curses build (Windows without windows-curses)
    picker = None


def same_name_rows() -> list:
    return [model.Row("claude", "skill", "tg", True), model.Row("claude", "command", "tg", True),
            model.Row("claude", "mcp", "tg", True), model.Row("claude", "skill", "other", True),
            model.Row("codex", "skill", "tg", True)]           # another harness: untouched


class ModelSameNameTest(unittest.TestCase):
    def test_toggle_same_name_flips_every_type_on_that_harness(self) -> None:
        rows = same_name_rows()
        self.assertEqual(model.toggle_same_name(rows, rows[0]), 3)
        self.assertEqual([r.staged for r in rows], [False, False, False, True, True])
        model.toggle_same_name(rows, rows[1])              # again: back to live
        self.assertEqual([r.changed for r in rows], [False] * 5)


@unittest.skipIf(picker is None, "curses unavailable")
class PickerSameNameTest(SandboxCase):
    def test_n_key_toggles_every_row_with_the_current_name(self) -> None:
        rows = same_name_rows()
        out = picker.loop(FakeWin(["n", "\n"]), rows, glyphs=theme.UNICODE)
        self.assertEqual(sorted((r.harness, r.type, r.name) for r in out),
                         [("claude", "command", "tg"), ("claude", "mcp", "tg"),
                          ("claude", "skill", "tg")])


class MenuSameNameTest(SandboxCase):
    def test_n_number_toggles_every_row_with_that_name(self) -> None:
        out = io.StringIO()
        changes = menu.loop(same_name_rows(), io.StringIO("n 1\na\n"), out)
        self.assertEqual(sorted(r.type for r in changes), ["command", "mcp", "skill"])
