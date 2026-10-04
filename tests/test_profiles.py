"""profile save / apply / diff / list: apply toggles only the items a profile mentions."""
from __future__ import annotations

import json
import os
import shutil
import unittest
from pathlib import Path

from test_cli_surface import CliCase, snapshot
from test_conformance import FIXTURES, file_bytes

from agent_toggle import fs

CLAUDE_ITEMS = (("skill", "demo-skill"), ("agent", "demo-agent"),
                ("command", "demo-command"), ("rule", "demo-rule"))
CODEX_ITEMS = (("skill", "demo-skill"), ("agent", "demo-agent"),
               ("command", "demo-command"), ("mcp", "example-mcp"))


class ProfileCase(CliCase):
    def setUp(self) -> None:
        super().setUp()
        self.cli_rc = 0
        for h in ("claude", "codex"):
            shutil.copytree(FIXTURES / h, self.tmp, dirs_exist_ok=True)

    def disable_all(self) -> None:
        for t, n in CLAUDE_ITEMS:
            self.assertEqual(self.run_cli("disable", t, n)[0], 0)
        for t, n in CODEX_ITEMS:
            self.assertEqual(self.run_cli("disable", t, n, "--harness", "codex")[0], 0)

    def profile(self, items: list[dict], **extra) -> Path:
        p = self.tmp / "p.json"
        p.write_text(json.dumps({"version": 1, "items": items, **extra}), encoding="utf-8")
        return p

    @staticmethod
    def item(harness: str, type_: str, name: str, live: bool = True) -> dict:
        return {"harness": harness, "type": type_, "name": name, "live": live}


class RoundTripTest(ProfileCase):
    def test_save_disable_apply_restores_bytes(self) -> None:
        before = file_bytes(self.tmp)
        self.assertEqual(self.run_cli("profile", "save", "work")[0], 0)
        self.disable_all()
        self.assertNotEqual(file_bytes(self.tmp), before)
        rc, env = self.run_json("profile", "apply", "work")
        self.assertEqual((rc, env["command"]), (0, "profile"))
        self.assertEqual(file_bytes(self.tmp), before)
        self.assertEqual(json.loads(fs.state_file().read_text(encoding="utf-8"))["disabled"], {})

    def test_saved_file_holds_only_the_four_fields(self) -> None:
        self.run_cli("profile", "save", "work")
        doc = json.loads((fs.profiles_dir() / "work.json").read_text(encoding="utf-8"))
        self.assertEqual(set(doc), {"version", "saved_at", "items"})
        self.assertEqual(doc["version"], 1)
        for it in doc["items"]:
            self.assertEqual(set(it), {"harness", "type", "name", "live"})
        self.assertIn(self.item("claude", "skill", "demo-skill"), doc["items"])
        self.assertNotIn(str(self.tmp), json.dumps(doc))

    def test_parked_items_are_saved_not_live(self) -> None:
        self.run_cli("disable", "skill", "demo-skill")
        self.run_cli("profile", "save", "work")
        doc = json.loads((fs.profiles_dir() / "work.json").read_text(encoding="utf-8"))
        self.assertIn(self.item("claude", "skill", "demo-skill", False), doc["items"])

    def test_parked_in_profile_but_live_now_gets_disabled(self) -> None:
        self.run_cli("disable", "skill", "demo-skill")
        self.run_cli("profile", "save", "lean")
        self.run_cli("enable", "skill", "demo-skill")
        self.assertEqual(self.run_cli("profile", "apply", "lean")[0], 0)
        self.assertFalse((self.home / "skills" / "demo-skill").exists())

    def test_out_file_round_trip(self) -> None:
        before = file_bytes(self.tmp)
        out = self.tmp / "dotfiles" / "work.json"
        out.parent.mkdir()
        self.assertEqual(self.run_cli("profile", "save", "work", "--out", str(out))[0], 0)
        self.assertFalse(fs.profiles_dir().exists())
        self.disable_all()
        self.assertEqual(self.run_cli("profile", "apply", str(out))[0], 0)
        self.assertEqual({k: v for k, v in file_bytes(self.tmp).items()
                          if not k.startswith("dotfiles")}, before)

    def test_harness_filter_limits_save_and_apply(self) -> None:
        self.run_cli("profile", "save", "c", "--harness", "codex")
        doc = json.loads((fs.profiles_dir() / "c.json").read_text(encoding="utf-8"))
        self.assertEqual({i["harness"] for i in doc["items"]}, {"codex"})


class FlagProfileTest(ProfileCase):
    """openclaw skills and opencode mcp are flag items: live in the dir/config, disabled by a flag."""

    def setUp(self) -> None:
        super().setUp()
        for h in ("openclaw", "opencode"):
            shutil.copytree(FIXTURES / h, self.tmp, dirs_exist_ok=True)

    def saved(self, name: str) -> list[dict]:
        return json.loads((fs.profiles_dir() / f"{name}.json").read_text(encoding="utf-8"))["items"]

    def test_flag_disabled_skill_round_trips_through_a_profile(self) -> None:
        before = file_bytes(self.tmp)
        self.assertEqual(self.run_cli("profile", "save", "all")[0], 0)
        self.assertIn(self.item("openclaw", "skill", "demo-skill"), self.saved("all"))
        self.assertEqual(self.run_cli("disable", "skill", "demo-skill", "--harness", "openclaw")[0], 0)
        self.assertEqual(self.run_cli("profile", "save", "off")[0], 0)
        self.assertIn(self.item("openclaw", "skill", "demo-skill", False), self.saved("off"))
        rc, env = self.run_json("profile", "apply", "all")
        self.assertEqual(rc, 0, env)
        self.assertEqual(file_bytes(self.tmp), before)

    def test_live_flag_items_are_saved_and_applied_without_skips(self) -> None:
        self.assertEqual(self.run_cli("profile", "save", "all")[0], 0)
        for it in (self.item("opencode", "mcp", "example-mcp"),
                   self.item("openclaw", "plugin", "demo-plugin")):
            self.assertIn(it, self.saved("all"))
        before = file_bytes(self.tmp)
        self.assertEqual(self.run_cli("disable", "mcp", "example-mcp", "--harness", "opencode")[0], 0)
        rc, env = self.run_json("profile", "apply", "all")
        self.assertEqual(rc, 0, env)
        self.assertNotIn("skipped", {r["status"] for r in env["results"]})
        self.assertEqual(file_bytes(self.tmp), before)

    def test_flag_item_profiled_off_is_not_reported_missing(self) -> None:
        self.assertEqual(self.run_cli("disable", "mcp", "example-mcp", "--harness", "opencode")[0], 0)
        self.assertEqual(self.run_cli("profile", "save", "off")[0], 0)
        self.assertIn(self.item("opencode", "mcp", "example-mcp", False), self.saved("off"))
        rc, out, _ = self.run_cli("profile", "apply", "off")
        self.assertEqual(rc, 0)
        self.assertNotIn("not on this machine", out)
        self.assertIn("already as profiled", out)


class ScopeTest(ProfileCase):
    def setUp(self) -> None:
        super().setUp()
        self.proj = self.tmp / "work" / "app"
        (self.proj / ".claude" / "skills" / "proj-skill").mkdir(parents=True)
        (self.proj / ".claude" / "skills" / "proj-skill" / "SKILL.md").write_text("x", encoding="utf-8")
        self.write("skills/proj-skill/SKILL.md")           # same name in user scope

    def test_project_profile_records_its_scope(self) -> None:
        self.assertEqual(self.run_cli("profile", "save", "p", "--project", str(self.proj))[0], 0)
        doc = json.loads((fs.profiles_dir() / "p.json").read_text(encoding="utf-8"))
        self.assertEqual(doc["scope"], "project")
        self.assertEqual(self.run_cli("profile", "save", "u")[0], 0)
        self.assertNotIn("scope", json.loads((fs.profiles_dir() / "u.json").read_text(encoding="utf-8")))

    def test_project_profile_never_applies_to_user_scope(self) -> None:
        self.run_cli("profile", "save", "p", "--project", str(self.proj))
        self.run_cli("disable", "skill", "proj-skill", "--project", str(self.proj))
        before = snapshot(self.tmp)
        for verb in ("apply", "diff"):
            rc, env = self.run_json("profile", verb, "p")
            self.assertEqual(rc, 2, env)
            self.assertIn("--project", json.dumps(env))
        self.assertEqual(snapshot(self.tmp), before)
        self.assertTrue((self.home / "skills" / "proj-skill").is_dir())      # user item untouched

    def test_user_profile_never_applies_to_a_project(self) -> None:
        self.run_cli("profile", "save", "u")
        self.assertEqual(self.run_cli("profile", "apply", "u", "--project", str(self.proj))[0], 2)

    def test_scope_key_must_be_user_or_project(self) -> None:
        p = self.profile([self.item("claude", "skill", "demo-skill")], scope="elsewhere")
        self.assertEqual(self.run_cli("profile", "apply", str(p))[0], 2)


class SaveFilterTest(ProfileCase):
    def test_names_apply_would_refuse_are_skipped_on_save_with_a_warning(self) -> None:
        self.claude_json({"mcpServers": {"team/search": {"command": "x"}, "ok-mcp": {"command": "y"}}})
        rc, env = self.run_json("profile", "save", "work")
        self.assertEqual(rc, 0, env)
        self.assertTrue(any("team/search" in w for w in env["warnings"]), env["warnings"])
        doc = json.loads((fs.profiles_dir() / "work.json").read_text(encoding="utf-8"))
        self.assertEqual([i["name"] for i in doc["items"] if i["type"] == "mcp"
                          and i["harness"] == "claude"], ["ok-mcp"])
        self.assertEqual(self.run_cli("profile", "apply", "work")[0], 0)
        self.assertEqual(self.run_cli("profile", "diff", "work")[0], 0)


class SemanticsTest(ProfileCase):
    def test_unknown_item_is_skipped_not_failed(self) -> None:
        p = self.profile([self.item("claude", "skill", "ghost-skill"),
                          self.item("grok", "skill", "demo-skill")])
        rc, env = self.run_json("profile", "apply", str(p))
        self.assertEqual(rc, 0)
        self.assertEqual([r["status"] for r in env["results"]], ["skipped", "skipped"])
        self.assertIn("not on this machine", env["results"][0]["detail"])
        self.assertIn("- skill ghost-skill not on this machine",
                      self.run_cli("profile", "apply", str(p))[1])

    def test_item_added_after_save_is_untouched(self) -> None:
        self.run_cli("profile", "save", "work")
        self.write("skills/late-skill/SKILL.md")
        self.write("skills/late-parked/SKILL.md")
        self.run_cli("disable", "skill", "late-parked")
        self.run_cli("disable", "skill", "demo-skill")
        self.assertEqual(self.run_cli("profile", "apply", "work")[0], 0)
        self.assertTrue((self.home / "skills" / "late-skill").is_dir())
        self.assertFalse((self.home / "skills" / "late-parked").exists())
        self.assertTrue((self.home / "skills" / "demo-skill").is_dir())

    def test_diff_is_read_only_and_names_the_plan(self) -> None:
        self.run_cli("profile", "save", "work")
        self.run_cli("disable", "skill", "demo-skill")
        before = snapshot(self.tmp)
        rc, env = self.run_json("profile", "diff", "work")
        self.assertEqual(rc, 0)
        self.assertEqual([(r["name"], r["action"], r["status"]) for r in env["results"]],
                         [("demo-skill", "would-enable", "planned")])
        self.assertEqual(snapshot(self.tmp), before)

    def test_dry_run_apply_writes_nothing(self) -> None:
        self.run_cli("profile", "save", "work")
        self.disable_all()
        before = snapshot(self.tmp)
        rc, env = self.run_json("profile", "apply", "work", "--dry-run")
        self.assertEqual(rc, 0)
        self.assertTrue(all(r["action"].startswith("would-") for r in env["results"]))
        self.assertEqual(snapshot(self.tmp), before)

    def test_apply_is_one_batch(self) -> None:
        self.run_cli("profile", "save", "work")
        self.disable_all()
        self.run_cli("profile", "apply", "work")
        rows = [json.loads(ln) for ln in
                fs.log_file().read_text(encoding="utf-8").splitlines()]
        applied = [r for r in rows if r["action"] == "enable"]
        self.assertEqual(len(applied), len(CLAUDE_ITEMS) + len(CODEX_ITEMS))
        self.assertEqual(len({r["batch"] for r in applied}), 1)

    def test_list(self) -> None:
        self.assertIn("no profiles", self.run_cli("profile", "list")[1])
        self.run_cli("profile", "save", "work")
        rc, env = self.run_json("profile", "list")
        self.assertEqual((rc, [r["name"] for r in env["results"]]), (0, ["work"]))


class ValidationTest(ProfileCase):
    def assert_usage(self, *argv: str) -> None:
        before = snapshot(self.tmp)
        rc, env = self.run_json(*argv)
        self.assertEqual(rc, 2, argv)
        self.assertFalse(env["ok"])
        self.assertEqual(snapshot(self.tmp), before)

    def test_dotdot_and_bad_names_exit_2(self) -> None:
        self.assert_usage("profile", "apply", "../x.json")
        self.assert_usage("profile", "apply", "..")
        self.assert_usage("profile", "save", "..")
        self.assert_usage("profile", "save", "a:b")
        self.assert_usage("profile", "save", "x.json")          # a path: use --out
        self.assert_usage("profile", "save", "nul")             # Windows device name
        self.assert_usage("profile", "save", "-x", "--out", str(self.tmp / "o.json"))
        self.assert_usage("profile", "save", "x", "--out", str(self.tmp))   # a directory

        self.assert_usage("profile", "save", "x", "--out", "../x.json")
        self.assert_usage("profile", "apply", "missing")

    def test_bad_documents_exit_2(self) -> None:
        good = self.item("claude", "skill", "demo-skill")
        for doc in (
            {"version": 2, "items": []},
            {"version": True, "items": []},
            {"version": 1, "items": [{**good, "harness": "nope"}]},
            {"version": 1, "items": [{**good, "type": "nope"}]},
            {"version": 1, "items": [{**good, "name": "../etc"}]},
            {"version": 1, "items": [{**good, "name": "/abs"}]},
            {"version": 1, "items": [{**good, "live": "yes"}]},
            {"version": 1, "items": [{**good, "path": "/x"}]},
            {"version": 1, "items": [good, {**good, "live": False}]},
            {"version": 1, "items": "x"},
            {"version": 1.0, "items": []},
            {"version": 1, "items": [{**good, "harness": []}]},
            {"version": 1, "items": [{**good, "type": {}}]},
            {"version": 1, "items": [], "extra": 1},
            [],
        ):
            with self.subTest(doc=doc):
                p = self.tmp / "bad.json"
                p.write_text(json.dumps(doc), encoding="utf-8")
                self.assert_usage("profile", "apply", str(p))
        (self.tmp / "bad.json").write_text("{nope", encoding="utf-8")
        self.assert_usage("profile", "apply", str(self.tmp / "bad.json"))

    def test_oversized_file_exit_2(self) -> None:
        p = self.tmp / "big.json"
        p.write_text(" " * (1 << 21), encoding="utf-8")
        self.assert_usage("profile", "apply", str(p))

    def test_deeply_nested_file_exit_2(self) -> None:
        p = self.tmp / "deep.json"
        p.write_text("[" * 100_000, encoding="utf-8")
        self.assert_usage("profile", "apply", str(p))

    def test_argument_misuse_exit_2(self) -> None:
        self.assert_usage("profile", "save")
        self.assert_usage("profile", "list", "x")
        self.assert_usage("profile", "apply", "x", "--out", "y.json")
        self.assert_usage("profile", "save", "x", "--dry-run")
        self.assert_usage("profile", "frobnicate")


@unittest.skipIf(os.name == "nt", "POSIX modes")
class ModeTest(ProfileCase):
    def test_stored_profile_is_private(self) -> None:
        self.run_cli("profile", "save", "work")
        self.assertEqual(fs.profiles_dir().stat().st_mode & 0o777, 0o700)
        self.assertEqual((fs.profiles_dir() / "work.json").stat().st_mode & 0o777, 0o600)


class McpOnlyProjectTest(ProfileCase):
    """A project with only `.mcp.json` (no `.claude/`) still lists its live servers."""

    def test_save_lists_live_and_parked_servers(self) -> None:
        proj = self.tmp / "work" / "bare"
        proj.mkdir(parents=True)
        (proj / ".mcp.json").write_text(json.dumps(
            {"mcpServers": {"alpha": {"command": "a"}, "beta": {"command": "b"}}}), encoding="utf-8")
        self.assertEqual(self.run_cli("disable", "mcp", "beta", "--project", str(proj))[0], 0)
        self.assertEqual(self.run_cli("profile", "save", "p", "--project", str(proj))[0], 0)
        doc = json.loads((fs.profiles_dir() / "p.json").read_text(encoding="utf-8"))
        self.assertEqual({(i["type"], i["name"]): i["live"] for i in doc["items"]},
                         {("mcp", "alpha"): True, ("mcp", "beta"): False})
        self.assertFalse((proj / ".claude").exists())


if __name__ == "__main__":
    unittest.main()
