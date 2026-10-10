"""Picker: the Mods group (plugins whose hooks/hooks.json has `modules`)."""
from __future__ import annotations

import io
import json
import subprocess
import unittest
from unittest import mock

from base import SandboxCase
from test_picker import FakeWin

from agent_toggle import cli, fs, ops, settings
from agent_toggle.backends import plugin_cli
from agent_toggle.harnesses import build
from agent_toggle.ui import menu, model, theme

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

    def test_cost_json_marks_the_mods(self) -> None:
        """The /agent-toggle skill finds mods through `cost --type plugin --json`."""
        listing = [self.plugin("pane", {"modules": ["./register.tsx"]}), self.plugin("bare", None)]
        plugin_cli.runner = lambda cmd, **kw: subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps(listing), stderr="")
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(cli.main(["cost", "--type", "plugin", "--json"]), 0)
        rows = json.loads(out.getvalue())["results"]
        self.assertEqual({r["name"]: r["mod"] for r in rows if r["name"]},
                         {"pane@mk": True, "bare@mk": False})

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
        for query in ("mod", "mods", "Mods"):            # the heading reads "Mods"
            self.assertEqual([r.name for r in model.match(rows, query)],
                             ["pane@mk", "weather@playground-mods"], query)


@unittest.skipIf(picker is None, "curses unavailable")
class PickerModsTest(SandboxCase):
    def test_mods_heading_is_drawn(self) -> None:
        rows = [model.Row("claude", "plugin", "pane@mk", True, mod=True)]
        win = FakeWin(["\x1b"])
        picker.loop(win, rows, glyphs=theme.UNICODE)
        self.assertGreaterEqual(win.find("Mods"), 0)


def ops_rows() -> list:
    """collect() order (harness, type, name); on screen: Skills, Plugins, Mods, MCP."""
    return [model.Row("claude", "mcp", "srv", True, 20),
            model.Row("claude", "plugin", "big@mk", True, 900, mod=True),
            model.Row("claude", "plugin", "off@mk", False, 0, 40, mod=True),
            model.Row("claude", "plugin", "pane@mk", True, 50, mod=True),
            model.Row("claude", "plugin", "plain@mk", True, 300),
            model.Row("claude", "skill", "alpha", True, 100),
            model.Row("codex", "skill", "beta", True, 10)]


MODS = ["t", "t", "t"]                  # All -> Skills -> Plugins -> Mods


@unittest.skipIf(picker is None, "curses unavailable")
class PickerModOpsTest(SandboxCase):
    """Every picker action on a mod behaves as it does on any other group."""

    def run_keys(self, keys, size=(30, 120)):
        self.rows = ops_rows()
        win = FakeWin(keys, size=size)
        return picker.loop(win, self.rows, glyphs=theme.UNICODE), win

    def test_space_parks_a_mod_and_enter_returns_it_as_a_plugin(self) -> None:
        out, _ = self.run_keys([*MODS, " ", "\n"])
        self.assertEqual([(r.type, r.name, r.staged) for r in out], [("plugin", "big@mk", False)])

    def test_space_restores_a_parked_mod(self) -> None:
        out, _ = self.run_keys([*MODS, picker.curses.KEY_DOWN, " ", "\n"])
        self.assertEqual([(r.name, r.staged) for r in out], [("off@mk", True)])

    def test_type_filter_cycles_through_mods_between_plugins_and_mcp(self) -> None:
        seen = []
        for n in range(1, 6):
            _, win = self.run_keys(["t"] * n + ["\x1b"])
            seen.append(win.screen[2].split("type: ")[1].split()[0])
        self.assertEqual(seen, ["Skills", "Plugins", "Mods", "MCP", "All"])
        _, win = self.run_keys([*MODS, "\x1b"])
        self.assertNotIn("plain@mk", win.text())
        self.assertNotIn("alpha", win.text())
        self.assertIn("Mods 3", win.text())

    def test_cost_sort_holds_inside_the_mods_group(self) -> None:
        _, win = self.run_keys(["s", "\x1b"])
        self.assertLess(win.find("plain@mk"), win.find("Mods 3"))
        self.assertLess(win.find("Mods 3"), win.find("big@mk"))
        self.assertLess(win.find("big@mk"), win.find("pane@mk"))
        self.assertLess(win.find("pane@mk"), win.find("off@mk"))     # parked (40) after live (50)
        self.assertLess(win.find("off@mk"), win.find("MCP 1"))

    def test_a_toggles_only_the_mods_under_the_mods_filter(self) -> None:
        out, _ = self.run_keys([*MODS, "a", "\n"])            # one parked -> all live
        self.assertEqual([(r.name, r.staged) for r in out], [("off@mk", True)])
        out, _ = self.run_keys([*MODS, "a", "a", "\n"])       # all live -> all parked
        self.assertEqual(sorted(r.name for r in out), ["big@mk", "pane@mk"])

    def test_harness_tabs_count_and_filter_mods_like_any_row(self) -> None:
        _, win = self.run_keys(["1", "\x1b"])                 # claude
        self.assertIn("Mods 3", win.text())
        _, win = self.run_keys(["2", "\x1b"])                 # codex
        self.assertNotIn("Mods", win.text())
        self.assertIn("beta", win.text())

    def test_saved_type_filter_mod_is_the_start_view(self) -> None:
        settings.set("picker_type", "mod")
        _, win = self.run_keys(["\x1b"])
        self.assertIn("type: Mods", win.screen[2])

    def test_detail_pane_names_the_mod(self) -> None:
        _, win = self.run_keys([*MODS, "\x1b"])
        self.assertRegex(win.text(), r"type\s+mod \(plugin\)")

    def test_search_keeps_the_mods_heading(self) -> None:
        _, win = self.run_keys(["/", "p", "a", "n", "e", "\x1b"])
        self.assertIn("Mods 1", win.text())
        self.assertIn("pane@mk", win.text())


class ModProfileAndApplyTest(SandboxCase):
    def test_a_profile_stages_a_mod_through_its_plugin_entry(self) -> None:
        fs.private_dir(fs.profiles_dir())
        (fs.profiles_dir() / "work.json").write_text(json.dumps({"version": 1, "items": [
            {"harness": "claude", "type": "plugin", "name": "pane@mk", "live": False}]}),
            encoding="utf-8")
        rows = ops_rows()
        self.assertIn("1 staged", model.profile_command(rows, "work"))
        self.assertEqual([r.name for r in rows if r.changed], ["pane@mk"])

    @unittest.skipIf(picker is None, "curses unavailable")
    def test_ui_applies_a_staged_mod_as_a_plugin_op(self) -> None:
        row = model.Row("claude", "plugin", "pane@mk", True, mod=True)
        row.staged = False
        with mock.patch.object(picker, "pick", return_value=[row]), \
                mock.patch.object(ops, "apply_plan", wraps=ops.apply_plan) as spy:
            self.assertEqual(cli.main(["ui", "--dry-run"]), 0)
        self.assertEqual(spy.call_args.args[0], [ops.Op("claude", "plugin", "disable", "pane@mk")])

    def test_menu_type_filter_reaches_mods_and_toggles_one(self) -> None:
        out = io.StringIO()
        changes = menu.loop(ops_rows(), io.StringIO("t\nt\nt\n1\na\n"), out)
        self.assertEqual([(r.name, r.staged) for r in changes], [("big@mk", False)])
        self.assertIn("Mods", out.getvalue())
