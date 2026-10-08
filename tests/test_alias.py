"""Two harnesses reading one real skills dir: parked once, both views stay consistent.

Fixture tests/fixtures/opencode-alias/ is a claude home plus an opencode home with no
skills dir; the alias is made at test time (a symlink, or opencode.json skills.paths).
"""
from __future__ import annotations

import json
import os
import shutil
import unittest
from pathlib import Path

from base import CAN_SYMLINK
from test_cli_surface import CliCase
from test_conformance import file_bytes

from agent_toggle.harnesses import build

FIXTURE = Path(__file__).parent / "fixtures" / "opencode-alias"
ITEM = "shared-skill"


class AliasBase(CliCase):
    def setUp(self) -> None:
        super().setUp()
        shutil.copytree(FIXTURE, self.tmp, dirs_exist_ok=True)
        self.oc = self.tmp / ".config" / "opencode"
        self.shared = self.tmp / ".claude" / "skills" / ITEM
        self.parked = self.tmp / ".claude" / "skills-disabled" / ITEM

    def state(self) -> dict:
        return json.loads((self.tmp / ".agent-toggle" / "state.json").read_text(encoding="utf-8"))["disabled"]

    def alias_via_paths(self) -> None:
        (self.oc / "opencode.json").write_text(
            json.dumps({"skills": {"paths": ["~/.claude/skills"]}}), encoding="utf-8")

    def check_parked_once(self) -> None:
        self.assertFalse(self.shared.exists())
        self.assertTrue((self.parked / "SKILL.md").is_file())
        self.assertFalse((self.oc / "skills-disabled").exists())     # no second park dir
        entry = self.state()[f"claude:skill:{ITEM}"]
        self.assertEqual(len(self.state()), 1)
        self.assertEqual((entry["harness"], entry["shared_with"]), ("claude", ["opencode"]))


class AliasBehaviour:
    """Mixed into one subclass per way of making the alias."""

    def test_disable_via_alias_parks_once_under_the_owner(self) -> None:
        before = file_bytes(self.tmp)
        rc, env = self.run_json("disable", "skill", ITEM, "--harness", "opencode")
        self.assertEqual(rc, 0)
        self.check_parked_once()
        row = env["results"][0]
        self.assertEqual((row["harness"], row["owner"], row["shared_with"]),
                         ("opencode", "claude", ["claude"]))
        self.assertIn("also affects claude", row["detail"])
        # the other side sees it gone and cannot park it a second time
        rc, _ = self.run_json("disable", "skill", ITEM)
        self.assertEqual(rc, 1)
        self.assertEqual(len(self.state()), 1)
        for h in ("opencode", "claude"):          # enable from EITHER side restores both views
            with self.subTest(enable_from=h):
                self.assertEqual(self.run_cli("enable", "skill", ITEM, "--harness", h)[0], 0)
                self.assertEqual(file_bytes(self.tmp), before)
                self.assertEqual(self.state(), {})
                self.run_cli("disable", "skill", ITEM, "--harness", "claude")

    def test_disable_from_owner_reports_the_alias(self) -> None:
        rc, env = self.run_json("disable", "skill", ITEM)
        self.assertEqual(rc, 0)
        self.assertEqual(env["results"][0]["shared_with"], ["opencode"])
        self.assertEqual(self.state()[f"claude:skill:{ITEM}"]["shared_with"], ["opencode"])
        self.assertEqual(self.run_cli("enable", "skill", ITEM, "--harness", "opencode")[0], 0)
        self.assertTrue(self.shared.is_dir())

    def test_status_and_list_show_the_sharing(self) -> None:
        rc, env = self.run_json("status")
        shared = {r["harness"]: r["parked"]["skill"]["shared_with"]
                  for r in env["results"] if r.get("parked") and "skill" in r["parked"]}
        self.assertEqual(shared, {"claude": ["opencode"], "opencode": ["claude"]})
        self.run_cli("disable", "skill", ITEM, "--harness", "opencode")
        rc, env = self.run_json("list")
        self.assertEqual(env["results"][0]["shared_with"], ["opencode"])
        self.assertIn("shared with opencode", self.run_cli("list")[1])
        self.assertEqual(len(self.run_json("list", "--harness", "opencode")[1]["results"]), 1)
        text = self.run_cli("status")[1]
        self.assertIn("shared dir with: claude", text)
        self.assertNotIn("untracked", text)       # alias view still recognises the parked item

    def test_sync_marker_warns(self) -> None:
        (self.tmp / ".claude" / "skills" / ".synced-from-test").write_text("", encoding="utf-8")
        rc, env = self.run_json("disable", "skill", ITEM, "--harness", "opencode")
        self.assertEqual(rc, 0)
        self.assertTrue(any(".synced-from-test" in w for w in env["warnings"]), env["warnings"])
        self.assertTrue(any(".synced-from-test" in w
                            for w in self.run_json("status")[1]["warnings"]))

    def test_sync_marker_warns_once_per_real_dir_naming_every_viewer(self) -> None:
        (self.tmp / ".claude" / "skills" / ".synced-from-test").write_text("", encoding="utf-8")
        hits = lambda env: [w for w in env["warnings"] if ".synced-from-test" in w]   # noqa: E731
        status = hits(self.run_json("status")[1])
        self.assertEqual(len(status), 1, status)
        self.assertIn("viewed by claude, opencode", status[0])
        disable = hits(self.run_json("disable", "skill", ITEM, "--harness", "opencode")[1])
        self.assertEqual(len(disable), 1, disable)
        self.assertIn("viewed by claude, opencode", disable[0])
        text = self.run_cli("status")[1]
        self.assertEqual(text.count(".synced-from-test"), 1, text)

    def test_gitignore_warning_once_per_park_dir_and_only_inside_a_work_tree(self) -> None:
        none = [w for w in self.run_json("disable", "skill", ITEM, "--harness", "opencode")[1]
                ["warnings"] if "NOT gitignored" in w]
        self.assertEqual(none, [])                      # no git work tree: nothing to fix
        self.run_cli("enable", "skill", ITEM, "--harness", "opencode")
        self.git_init()
        warned = [w for w in self.run_json("disable", "skill", ITEM, "--harness", "opencode")[1]
                  ["warnings"] if "NOT gitignored" in w]
        self.assertEqual(len(warned), 1, warned)
        self.assertEqual(len(set(warned)), 1)
        self.assertIn(f"{(self.tmp / '.claude').resolve()}/.gitignore", warned[0])


@unittest.skipUnless(CAN_SYMLINK, "cannot create symlinks here")
class SymlinkAliasTest(AliasBehaviour, AliasBase):
    def setUp(self) -> None:
        super().setUp()
        (self.oc / "skills").symlink_to(self.tmp / ".claude" / "skills", target_is_directory=True)


@unittest.skipUnless(CAN_SYMLINK, "cannot create symlinks here")
class DotfilesAliasTest(AliasBehaviour, AliasBase):
    """Both harnesses symlink to a third dir: the owner must not depend on who asks."""

    def setUp(self) -> None:
        super().setUp()
        dotfiles = self.tmp / "dotfiles" / "skills"
        dotfiles.parent.mkdir()
        shutil.move(self.tmp / ".claude" / "skills", dotfiles)
        (self.tmp / ".claude" / "skills").symlink_to(dotfiles, target_is_directory=True)
        (self.oc / "skills").symlink_to(dotfiles, target_is_directory=True)


class SkillsPathsAliasTest(AliasBehaviour, AliasBase):
    def setUp(self) -> None:
        super().setUp()
        self.alias_via_paths()


class DefaultCompatAliasTest(AliasBehaviour, AliasBase):
    """No skills.paths at all: OpenCode still scans ~/.claude/skills, so claude's
    skills are a shared dir (parked once under claude), not a second park dir."""

    def test_cost_counts_the_shared_skill_once(self) -> None:
        rc, env = self.run_json("cost")
        names = [(r["harness"], r["name"]) for r in env["results"]
                 if r.get("type") == "skill" and r.get("name") == ITEM]
        self.assertEqual(names, [("claude", ITEM)])


class OpencodeTableTest(AliasBase):
    def test_xdg_config_home_wins_and_relative_is_ignored(self) -> None:
        self.assertEqual(build(self.tmp)["opencode"].home, self.oc)
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp / "xdg")
        self.assertEqual(build(self.tmp)["opencode"].home, self.tmp / "xdg" / "opencode")
        os.environ["XDG_CONFIG_HOME"] = "relative/dir"
        self.assertEqual(build(self.tmp)["opencode"].home, self.oc)

    def dirs(self) -> tuple[str, ...]:
        return build(self.tmp)["opencode"].dirs["skill"]

    def write_cfg(self, text: str, name: str = "opencode.json") -> None:
        (self.oc / name).write_text(text, encoding="utf-8")

    def test_skill_dirs_default_and_additive_and_survive_bad_json(self) -> None:
        claude, agents = str(self.tmp / ".claude/skills"), str(self.tmp / ".agents/skills")
        absdir = str(self.tmp / "abs" / "dir")
        default = ("skills", "skill", claude, agents)      # T1: OpenCode 2.0.22 scans all four
        self.assertEqual(self.dirs(), default)
        self.write_cfg("{ not json")
        self.assertEqual(self.dirs(), default)
        self.write_cfg(json.dumps({"skills": {"paths": [
            "~/.codex/skills", "rel", "~other/x", 7, "~/.codex/skills", absdir]}}))
        # redirects first (install-shims target), own dirs stay scanned, relative skipped
        self.assertEqual(self.dirs(), (str(self.tmp / ".codex/skills"), absdir, *default))
        (self.oc / "opencode.json").write_bytes(b"\xff\xfe{")      # not UTF-8: still no crash
        self.assertEqual(self.dirs(), default)
        shutil.rmtree(self.oc)                  # not installed: no other harness's dirs
        self.assertEqual(self.dirs(), ("skills", "skill"))

    def test_skills_paths_forms_jsonc_and_home_vars(self) -> None:
        codex = str(self.tmp / ".codex/skills")
        tail = self.dirs()[-4:]
        self.write_cfg('// c\n{"skills": {"paths": ["$HOME/.codex/skills", /* x */ "${HOME}/a", "~"],}}',
                       "opencode.jsonc")
        self.assertEqual(self.dirs(), (codex, str(self.tmp / "a"), str(self.tmp), *tail))
        self.write_cfg('{"skills": ["~/.codex/skills"]}', "opencode.jsonc")      # bare list
        self.assertEqual(self.dirs(), (codex, *tail))
        self.write_cfg('{"skills": {"paths": ["~/from-json"]}}')                 # .json wins
        self.assertEqual(self.dirs()[0], str(self.tmp / "from-json"))

    def test_unshared_opencode_items_report_no_sharing(self) -> None:
        rc, env = self.run_json("disable", "command", "own-command", "--harness", "opencode")
        self.assertEqual(rc, 0)
        self.assertNotIn("shared_with", env["results"][0])
        self.assertNotIn("shared_with", self.state()["opencode:command:own-command"])
        self.assertTrue((self.oc / "command-disabled" / "own-command.md").is_file())
