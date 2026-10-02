"""Token estimates: per-type formulas, the `cost` command, plugin rows, ui --dry-run."""
from __future__ import annotations

import json
import subprocess
import time
import unittest
from unittest import mock

from test_cli_surface import CliCase, snapshot

from agent_toggle import cost, fs
from agent_toggle.backends import plugin_cli
from agent_toggle.harnesses import build

FM = "---\nname: {n}\ndescription: {d}\n---\n{body}"


class EstimatorTest(CliCase):
    def est(self, type_: str, rel: str, text: str, name: str = "n"):
        return cost.file_estimate(type_, name, self.write(rel, text))

    def test_skill_counts_name_and_description_not_the_body(self) -> None:
        tokens, chars, basis = self.est(
            "skill", "skills/s/SKILL.md", FM.format(n="abcd", d="x" * 96, body="B" * 50000))
        self.assertEqual((tokens, chars), (25, 100))
        self.assertIn("name+description 100 chars", basis)

    def test_folded_description_and_missing_frontmatter(self) -> None:
        folded = "---\nname: f\ndescription: >\n  one two\n  three\n---\n"
        self.assertEqual(self.est("skill", "skills/f/SKILL.md", folded)[1], len("f") + len("one two three"))
        tokens, chars, basis = self.est("skill", "skills/g/SKILL.md", "# no frontmatter", name="gname")
        self.assertEqual(chars, len("gname"))
        self.assertIn("no frontmatter", basis)

    def test_agent_adds_tools_command_adds_hint_and_filename(self) -> None:
        agent = "---\nname: a\ndescription: dd\ntools: Read, Bash\n---\nbody"
        self.assertEqual(self.est("agent", "agents/a.md", agent)[1], 1 + 2 + len("Read, Bash"))
        cmd = "---\ndescription: dd\nargument-hint: [x]\n---\nbody"
        self.assertEqual(self.est("command", "commands/go.md", cmd, name="go")[1], 2 + 3 + 2)

    def test_rule_counts_the_whole_file(self) -> None:
        self.assertEqual(self.est("rule", "rules/r.md", "x" * 400)[:2], (100, 400))

    def test_head_is_capped(self) -> None:
        # an unterminated frontmatter must not make us read the whole file
        big = "---\nname: n\n" + "k: v\n" * 100000
        with mock.patch.object(cost, "HEAD_CAP", 64):
            self.assertLess(self.est("skill", "skills/big/SKILL.md", big)[1], 200)

    def test_mcp_weights(self) -> None:
        self.assertEqual(cost.mcp_estimate("claude", 6)[0], 60)           # names only
        self.assertEqual(cost.mcp_estimate("claude", None)[0], 20)
        self.assertEqual(cost.mcp_estimate("codex", 6)[0], 1800)
        self.assertEqual(cost.mcp_estimate("codex", None)[0], 1500)

    def test_toml_tool_tables_set_the_tool_count(self) -> None:
        (self.tmp / ".codex").mkdir()
        (self.tmp / ".codex" / "config.toml").write_text(
            '[mcp_servers.a]\ncommand = "x"\n[mcp_servers.a.tools.t1]\nx = 1\n'
            "[mcp_servers.a.tools.t2]\nx = 1\n[mcp_servers.b]\ncommand = \"y\"\n", encoding="utf-8")
        est = {i.name: i for i in cost.inventory({"disabled": {}}, build(self.tmp))
               if i.type == "mcp"}
        self.assertEqual((est["a"].tokens, est["b"].tokens), (600, 1500))


class CostCommandTest(CliCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("skills/big/SKILL.md", FM.format(n="big", d="d" * 397, body=""))
        self.write("skills/small/SKILL.md", FM.format(n="small", d="d", body=""))
        self.write("rules/rule.md", "r" * 80)
        self.claude_json({"mcpServers": {"srv": {"command": "x"}}})

    def results(self, *argv: str) -> list[dict]:
        rc, env = self.run_json(*argv)
        self.assertEqual(rc, 0, env)
        return env["results"]

    def test_sorted_by_tokens_descending_with_basis_and_total(self) -> None:
        rows = [r for r in self.results("cost") if r["name"]]
        tokens = [r["tokens"] for r in rows]
        self.assertEqual(tokens, sorted(tokens, reverse=True))
        self.assertEqual(rows[0]["name"], "big")
        self.assertEqual(rows[0]["tokens"], 100)
        self.assertIn("chars", rows[0]["detail"])
        total = self.results("cost")[-1]
        self.assertEqual(total["total_tokens"], sum(tokens))
        self.assertEqual(total["formula"]["chars_per_token"], 4)

    def test_parked_costs_zero_and_reports_would_save(self) -> None:
        self.assertEqual(self.run_cli("disable", "skill", "big")[0], 0)
        rows = {r["name"]: r for r in self.results("cost") if r["name"]}
        self.assertEqual((rows["big"]["tokens"], rows["big"]["would_save"], rows["big"]["enabled"]),
                         (0, 100, False))
        self.assertEqual(self.results("cost")[-1]["saved_tokens"], 100)
        self.assertIn("would save ~100", self.run_cli("cost")[1])

    def test_both_spellings_match_and_it_is_read_only(self) -> None:
        before = snapshot(self.tmp)
        for argv in (["cost"], ["cost", "--harness", "claude"], ["cost", "--type", "rule"]):
            self.assertEqual(self.run_cli(*argv, "--json"),
                             self.run_cli(f"--{argv[0]}", *argv[1:], "--json"))
        self.assertEqual(snapshot(self.tmp), before)           # no state file, no lock, no log
        self.assertFalse(fs.state_file().exists())

    def test_type_and_harness_filters(self) -> None:
        names = {r["name"] for r in self.results("cost", "--type", "rule") if r["name"]}
        self.assertEqual(names, {"rule"})
        self.assertEqual([r for r in self.results("cost", "--harness", "codex") if r["name"]], [])

    def test_shared_dir_is_one_row_under_its_owner(self) -> None:
        oc = self.tmp / ".config" / "opencode"
        oc.mkdir(parents=True)
        (oc / "opencode.json").write_text(json.dumps({"skills": {"paths": ["~/.claude/skills"]}}), encoding="utf-8")
        for harness in ("claude", "opencode"):
            rows = [r for r in self.results("cost", "--type", "skill", "--harness", harness)
                    if r["name"] == "big"]
            self.assertEqual([(r["harness"], r["shared_with"]) for r in rows],
                             [("claude", ["opencode"])])
        everything = [r for r in self.results("cost") if r["name"] == "big"]
        self.assertEqual(len(everything), 1)

    def test_plugin_rows_come_from_the_runner(self) -> None:
        live, off = self.tmp / "plug-live", self.tmp / "plug-off"
        for root in (live, off):
            (root / "skills" / "ps").mkdir(parents=True)
            (root / "skills" / "ps" / "SKILL.md").write_text(FM.format(n="ps", d="d" * 38, body=""), encoding="utf-8")
            (root / "commands").mkdir()
            (root / "commands" / "c.md").write_text("---\ndescription: dddd\n---\n", encoding="utf-8")
        (live / ".mcp.json").write_text(json.dumps({"mcpServers": {"m": {}}}), encoding="utf-8")
        listing = [{"id": "live@mk", "enabled": True, "installPath": str(live)},
                   {"id": "off@mk", "enabled": False, "installPath": str(off)}]
        calls = []

        def runner(cmd, **kw):
            calls.append(cmd[1:])
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(listing), stderr="")

        plugin_cli.runner = runner
        rows = {r["name"]: r for r in self.results("cost", "--type", "plugin") if r["name"]}
        self.assertEqual(calls, [["plugin", "list", "--json"]])
        # skill 2+38 chars = 10 tok; command 'c'+'dddd' = 5 chars = 2 tok; one mcp server = 20 tok
        self.assertEqual(rows["live@mk"]["tokens"], 10 + 2 + 20)
        self.assertEqual((rows["off@mk"]["tokens"], rows["off@mk"]["would_save"]), (0, 12))

    def test_plugin_list_tolerates_stderr_noise(self) -> None:
        listing = [{"id": "p@mk", "enabled": True}]
        plugin_cli.runner = lambda cmd, **kw: subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps(listing), stderr="update available\n")
        rows = [r for r in self.results("cost", "--type", "plugin") if r["name"]]
        self.assertEqual([r["name"] for r in rows], ["p@mk"])

    def test_plugin_list_failure_warns_but_succeeds(self) -> None:
        rc, env = self.run_json("cost")         # base stub: the CLI exits non-zero
        self.assertEqual(rc, 0)
        self.assertTrue(any("plugin list" in w for w in env["warnings"]))


class PerfAndDryRunTest(CliCase):
    def test_200_items_is_millisecond_scale(self) -> None:
        for i in range(200):
            self.write(f"skills/s{i}/SKILL.md", FM.format(n=f"s{i}", d="d" * 300, body="B" * 20000))
        start = time.perf_counter()
        items = cost.inventory({"disabled": {}}, build(self.tmp))
        elapsed = time.perf_counter() - start
        self.assertEqual(sum(1 for i in items if i.type == "skill"), 200)
        self.assertLess(elapsed, 1.0)          # measured ~tens of ms; 1 s is the DESIGN ceiling

    def test_ui_dry_run_plans_and_writes_nothing(self) -> None:
        try:
            from agent_toggle.ui import picker
        except ImportError:
            self.skipTest("curses unavailable")
        self.write("skills/demo-skill/SKILL.md")
        before = snapshot(self.tmp)
        row = picker.Row("claude", "skill", "demo-skill", True)
        row.staged = False
        with mock.patch.object(picker, "pick", return_value=[row]) as pick:
            rc, out, _ = self.run_cli("ui", "--dry-run")
        self.assertIs(pick.call_args.kwargs["plugins"], False)
        self.assertEqual(rc, 0)
        self.assertIn("would disable", out)
        self.assertIn("dry run", out)
        self.assertEqual(self.cli_calls, [])          # a dry run never shells out to claude
        self.assertEqual(snapshot(self.tmp), before)
        self.assertFalse(fs.state_file().exists())


if __name__ == "__main__":
    unittest.main()
