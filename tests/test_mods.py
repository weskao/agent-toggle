"""Picker: the Mods group (plugins whose hooks/hooks.json has `modules`)."""
from __future__ import annotations

import json
import subprocess
import unittest

from base import SandboxCase
from test_picker import FakeWin

from agent_toggle.backends import plugin_cli
from agent_toggle.harnesses import build
from agent_toggle.ui import model, theme

try:
    from agent_toggle.ui import picker
except ImportError:              # no curses build (Windows without windows-curses)
    picker = None


class ModsTest(SandboxCase):
    def plugin(self, name: str, hooks: dict | None) -> dict:
        root = self.tmp / "plugins" / name
        (root / "hooks").mkdir(parents=True)
        if hooks is not None:
            (root / "hooks" / "hooks.json").write_text(json.dumps(hooks), encoding="utf-8")
        return {"id": f"{name}@mk", "enabled": True, "installPath": str(root)}

    def test_collect_marks_a_plugin_with_modules_as_a_mod(self) -> None:
        listing = [self.plugin("pane", {"modules": ["./register.tsx"]}),
                   self.plugin("hooky", {"hooks": {"PreToolUse": []}}),
                   self.plugin("bare", None)]
        plugin_cli.runner = lambda cmd, **kw: subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps(listing), stderr="")
        rows = model.collect({"version": 3, "disabled": {}}, {"claude": build(self.tmp)["claude"]})
        groups = {r.name: (r.type, r.group) for r in rows if r.type == "plugin"}
        self.assertEqual(groups, {"pane@mk": ("plugin", "mod"), "hooky@mk": ("plugin", "plugin"),
                                  "bare@mk": ("plugin", "plugin")})

    def test_mods_group_after_plugins_and_filter_by_type(self) -> None:
        rows = [model.Row("claude", "mcp", "m", True), model.Row("claude", "plugin", "p", True),
                model.Row("claude", "plugin", "x", True, mod=True)]
        self.assertEqual([g for g, _ in model.grouped(rows)], ["plugin", "mod", "mcp"])
        self.assertEqual([r.name for r in model.visible(rows, type_="mod")], ["x"])
        self.assertEqual([r.name for r in model.visible(rows, type_="plugin")], ["p"])
        self.assertEqual(model.type_label("mod"), "Mods")


@unittest.skipIf(picker is None, "curses unavailable")
class PickerModsTest(SandboxCase):
    def test_mods_heading_is_drawn(self) -> None:
        rows = [model.Row("claude", "plugin", "pane@mk", True, mod=True)]
        win = FakeWin(["\x1b"])
        picker.loop(win, rows, glyphs=theme.UNICODE)
        self.assertGreaterEqual(win.find("Mods"), 0)
