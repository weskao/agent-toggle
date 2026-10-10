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

    def cached(self, pid: str, version: str = "1.0.0") -> None:
        """A mod in the plugin cache: ~/.claude/plugins/cache/<marketplace>/<name>/<version>."""
        name, _, market = pid.partition("@")
        hooks = self.home / "plugins" / "cache" / market / name / version / "hooks"
        hooks.mkdir(parents=True)
        (hooks / "hooks.json").write_text('{ "modules": ["./register.tsx"] }', encoding="utf-8")

    def plugin_groups(self, listing: list, state: dict | None = None) -> dict:
        plugin_cli.runner = lambda cmd, **kw: subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps(listing), stderr="")
        rows = model.collect(state or {"version": 3, "disabled": {}},
                             {"claude": build(self.tmp)["claude"]})
        return {r.name: r.group for r in rows if r.type == "plugin"}

    def test_a_mod_in_the_cache_counts_even_when_installPath_points_elsewhere(self) -> None:
        self.cached("flow@mk")
        listing = [{"id": "flow@mk", "enabled": True,
                    "installPath": str(self.tmp / "elsewhere")},     # no hooks.json there
                   {"id": "plain@mk", "enabled": True}]               # no installPath at all
        self.assertEqual(self.plugin_groups(listing), {"flow@mk": "mod", "plain@mk": "plugin"})

    def test_a_parked_mod_missing_from_the_listing_is_still_a_mod(self) -> None:
        self.cached("weather@mk")
        state = {"version": 3, "disabled": {"claude:plugin:weather@mk": {
            "harness": "claude", "type": "plugin", "name": "weather@mk", "mechanism": "cli"}}}
        self.assertEqual(self.plugin_groups([], state), {"weather@mk": "mod"})

    def test_mods_group_after_plugins_and_filter_by_type(self) -> None:
        rows = [model.Row("claude", "mcp", "m", True), model.Row("claude", "plugin", "p", True),
                model.Row("claude", "plugin", "x", True, mod=True)]
        self.assertEqual([g for g, _ in model.grouped(rows)], ["plugin", "mod", "mcp"])
        self.assertEqual([r.name for r in model.visible(rows, type_="mod")], ["x"])
        self.assertEqual([r.name for r in model.visible(rows, type_="plugin")], ["p"])
        self.assertEqual(model.type_label("mod"), "Mods")

    def test_typing_mod_finds_every_mod_not_just_names_with_mod(self) -> None:
        rows = [model.Row("claude", "plugin", "pane@mk", True, mod=True),
                model.Row("claude", "plugin", "weather@playground-mods", True, mod=True),
                model.Row("claude", "plugin", "plain@mk", True)]
        self.assertEqual([r.name for r in model.match(rows, "mod")],
                         ["pane@mk", "weather@playground-mods"])


@unittest.skipIf(picker is None, "curses unavailable")
class PickerModsTest(SandboxCase):
    def test_mods_heading_is_drawn(self) -> None:
        rows = [model.Row("claude", "plugin", "pane@mk", True, mod=True)]
        win = FakeWin(["\x1b"])
        picker.loop(win, rows, glyphs=theme.UNICODE)
        self.assertGreaterEqual(win.find("Mods"), 0)
