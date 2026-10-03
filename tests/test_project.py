"""--project <dir> scope for dir types (DESIGN s5.5, s6.1 rows 1-2).

Parked project items live under ~/.agent-toggle/parked/<sha8>/, never inside
the project; project state keys and log rows never mix with user scope.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from base import CAN_SYMLINK
from test_cli_surface import CliCase, snapshot
from test_conformance import file_bytes

from agent_toggle import fs, harnesses, store


class ProjectCase(CliCase):
    def setUp(self) -> None:
        super().setUp()
        self.proj = self.tmp / "work" / "app"
        self.pclaude = self.proj / ".claude"
        self.pskill = self.pclaude / "skills" / "demo-skill"
        self.pskill.mkdir(parents=True)
        (self.pskill / "SKILL.md").write_text("---\nname: demo-skill\n---\nproject copy é\n",
                                              encoding="utf-8")
        (self.pskill / "ref").mkdir()
        (self.pskill / "ref" / "notes.md").write_text("nested\n", encoding="utf-8")
        (self.pclaude / "agents").mkdir()
        (self.pclaude / "agents" / "demo-agent.md").write_text("agent\n", encoding="utf-8")
        (self.proj / "README.md").write_text("repo\n", encoding="utf-8")
        self.write("skills/demo-skill/SKILL.md", "user copy\n")
        self.digest = store.project_digest(self.proj)
        self.key = store.make_key("claude", "skill", "demo-skill", project=self.proj)

    def p(self, *argv: str, project: Path | None = None) -> tuple[int, dict]:
        return self.run_json(*argv, "--project", str(project or self.proj))

    def state(self) -> dict:
        if not fs.state_file().exists():
            return {}
        return json.loads(fs.state_file().read_text(encoding="utf-8"))["disabled"]

    def parked(self, *rel: str) -> Path:
        return fs.parked_dir().joinpath(self.digest, *rel)


class ProjectDisableTest(ProjectCase):
    def test_disable_parks_outside_the_project(self) -> None:
        rc, env = self.p("disable", "skill", "demo-skill")
        self.assertEqual(rc, 0, env)
        self.assertFalse(self.pskill.exists())
        self.assertTrue((self.parked("skills-disabled", "demo-skill", "SKILL.md")).is_file())
        self.assertEqual([p for p in self.proj.rglob("*") if "disabled" in p.name], [])
        entry = self.state()[self.key]
        self.assertEqual(self.key, f"claude@{self.digest}:skill:demo-skill")
        self.assertEqual(entry["project"], str(self.proj.resolve()))
        self.assertTrue(Path(entry["parked_at"]).is_relative_to(fs.parked_dir() / self.digest))
        view = {"claude": harnesses.project_view(self.proj)}
        self.assertIsNone(store.check_entry(entry, view, self.key))
        self.assertTrue((self.home / "skills" / "demo-skill" / "SKILL.md").is_file())  # user copy
        self.assertNotIn("claude:skill:demo-skill", self.state())

    def test_git_warning(self) -> None:
        rc, env = self.p("disable", "skill", "demo-skill")
        self.assertEqual(rc, 0)
        rproj = self.proj.resolve()                       # macOS: /var -> /private/var
        want = (f"{rproj / '.claude/skills/demo-skill'} is a tracked deletion in git status; "
                f"restore with: agent-toggle enable skill demo-skill --project {rproj}")
        self.assertIn(want, env["warnings"])
        self.run_cli("enable", "skill", "demo-skill", "--project", str(self.proj))
        _, _, err = self.run_cli("disable", "skill", "demo-skill", "--project", str(self.proj))
        self.assertEqual(err.count("tracked deletion in git status"), 1)

    def test_round_trip_is_byte_identical(self) -> None:
        before, home_before = file_bytes(self.proj), file_bytes(self.home)
        for t, n in (("skill", "demo-skill"), ("agent", "demo-agent")):
            self.assertEqual(self.p("disable", t, n)[0], 0)
        self.assertNotEqual(file_bytes(self.proj), before)
        for t, n in (("skill", "demo-skill"), ("agent", "demo-agent")):
            rc, env = self.p("enable", t, n)
            self.assertEqual(rc, 0, env)
        self.assertEqual(file_bytes(self.proj), before)
        self.assertEqual(file_bytes(self.home), home_before)
        self.assertEqual(self.state(), {})

    def test_companions_never_leave_the_repo(self) -> None:
        hook = self.pclaude / "hooks" / "guard.sh"
        hook.parent.mkdir()
        hook.write_text("#!/bin/sh\n", encoding="utf-8")
        (self.pskill / "SKILL.md").write_text("run .claude/hooks/guard.sh and hooks/guard.sh\n",
                                              encoding="utf-8")
        rc, env = self.p("disable", "skill", "demo-skill")
        self.assertEqual(rc, 0, env)
        self.assertTrue(hook.is_file())
        self.assertEqual(self.state()[self.key]["companions"], [])

    def test_dry_run_warning_is_a_preview(self) -> None:
        rc, env = self.p("disable", "skill", "demo-skill", "--dry-run")
        self.assertTrue(any("would be a tracked deletion" in w for w in env["warnings"]))

    def test_log_rows_carry_the_project_scope(self) -> None:
        self.p("disable", "skill", "demo-skill")
        self.p("disable", "skill", "ghost")
        rows = [json.loads(ln) for ln in fs.log_file().read_text(encoding="utf-8").splitlines()]
        self.assertEqual({(r["scope"], r["project"]) for r in rows},
                         {("project", str(self.proj.resolve()))})

    def test_dry_run_writes_nothing(self) -> None:
        before = snapshot(self.tmp)
        rc, env = self.p("disable", "skill", "demo-skill", "--dry-run")
        self.assertEqual(rc, 0, env)
        self.assertEqual(env["results"][0]["action"], "would-disable")
        self.assertEqual(snapshot(self.tmp), before)

    def test_bad_names_exit_2(self) -> None:
        for name in ("../demo-skill", str(self.pskill), "a/../b", "-x"):
            self.assertEqual(self.p("disable", "skill", name)[0], 2, name)

    def test_unusable_project_dirs(self) -> None:
        self.assertEqual(self.p("disable", "skill", "x", "--harness", "codex")[0], 4)
        self.assertEqual(self.p("disable", "plugin", "x")[0], 4)         # no plugin in project scope
        self.assertEqual(self.p("disable", "skill", "x", project=self.tmp / "nope")[0], 4)
        (self.tmp / "bare").mkdir()
        self.assertEqual(self.p("disable", "skill", "x", project=self.tmp / "bare")[0], 4)
        nested = self.home / "skills" / "demo-skill"                    # inside ~/.claude
        (nested / ".claude").mkdir()
        for bad in (self.tmp, self.tmp.parent, nested):                  # $HOME and above
            self.assertEqual(self.p("disable", "skill", "demo-skill", project=bad)[0], 2, bad)
        self.assertTrue((self.home / "skills" / "demo-skill").exists())

    def test_dot_is_the_cwd(self) -> None:
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(self.proj)
        rc, env = self.run_json("disable", "skill", "demo-skill", "--project", ".")
        self.assertEqual(rc, 0, env)
        self.assertIn(self.key, self.state())

    @unittest.skipUnless(CAN_SYMLINK, "cannot create symlinks here")
    def test_symlinked_project_dirs_into_user_scope_are_refused(self) -> None:
        shutil.rmtree(self.pclaude / "skills")
        (self.pclaude / "skills").symlink_to(self.home / "skills")
        rc, env = self.p("disable", "skill", "demo-skill")
        self.assertEqual(rc, 1, env)
        self.assertIn("outside the project", env["results"][0]["detail"])
        self.assertTrue((self.home / "skills" / "demo-skill").exists())
        self.assertEqual(self.p("profile", "save", "p")[0], 0)        # user skills not listed
        doc = json.loads((fs.profiles_dir() / "p.json").read_text(encoding="utf-8"))
        self.assertEqual([i for i in doc["items"] if i["type"] == "skill"], [])
        shutil.rmtree(self.pclaude)
        self.pclaude.symlink_to(self.home)
        self.assertEqual(self.p("disable", "skill", "demo-skill")[0], 2)
        self.assertTrue((self.home / "skills" / "demo-skill").exists())
        self.assertEqual(self.state(), {})


class ProjectScopeIsolationTest(ProjectCase):
    def test_user_scope_never_restores_a_project_item(self) -> None:
        self.assertEqual(self.run_cli("disable", "skill", "demo-skill")[0], 0)     # user scope
        self.assertEqual(self.p("disable", "skill", "demo-skill")[0], 0)
        self.assertEqual(sorted(self.state()), sorted(["claude:skill:demo-skill", self.key]))
        self.assertEqual(self.run_cli("enable", "skill", "demo-skill")[0], 0)
        self.assertTrue((self.home / "skills" / "demo-skill").exists())
        self.assertFalse(self.pskill.exists())
        self.assertEqual(list(self.state()), [self.key])
        self.assertEqual(self.p("enable", "skill", "demo-skill")[0], 0)
        self.assertTrue(self.pskill.exists())

    def test_project_enable_never_touches_a_user_entry(self) -> None:
        self.assertEqual(self.run_cli("disable", "skill", "demo-skill")[0], 0)
        rc, env = self.p("enable", "skill", "demo-skill")
        self.assertEqual(rc, 1)
        self.assertIn("nothing parked", env["results"][0]["detail"])
        self.assertEqual(list(self.state()), ["claude:skill:demo-skill"])

    def test_tampered_entry_parked_inside_the_repo_is_refused(self) -> None:
        self.p("disable", "skill", "demo-skill")
        inside = self.pclaude / "skills-disabled"
        inside.mkdir()
        shutil.move(str(self.parked("skills-disabled", "demo-skill")), str(inside))
        state = json.loads(fs.state_file().read_text(encoding="utf-8"))
        state["disabled"][self.key]["parked_at"] = str(inside / "demo-skill")
        fs.state_file().write_text(json.dumps(state), encoding="utf-8")
        before, entries = snapshot(self.tmp), self.state()
        rc, env = self.p("enable", "skill", "demo-skill")
        self.assertEqual(rc, 1)
        self.assertIn("refused", env["results"][0]["detail"])
        mine = ("log.jsonl", "state.json")                    # rewritten, same entries
        self.assertEqual({k: v for k, v in snapshot(self.tmp).items() if not k.endswith(mine)},
                         {k: v for k, v in before.items() if not k.endswith(mine)})
        self.assertEqual(self.state(), entries)

    def test_tampered_origin_into_user_home_is_refused(self) -> None:
        self.p("disable", "skill", "demo-skill")
        state = json.loads(fs.state_file().read_text(encoding="utf-8"))
        state["disabled"][self.key]["origin"] = str(self.home / "skills" / "demo-skill")
        fs.state_file().write_text(json.dumps(state), encoding="utf-8")
        rc, env = self.p("enable", "skill", "demo-skill")
        self.assertEqual(rc, 1)
        self.assertIn("refused", env["results"][0]["detail"])

    def test_list_status_and_cost_keep_scopes_apart(self) -> None:
        self.p("disable", "skill", "demo-skill")
        rc, env = self.run_json("list")
        self.assertEqual([r.get("project") for r in env["results"]], [str(self.proj.resolve())])
        rc, env = self.run_json("list", "--project", str(self.proj))
        self.assertEqual(len(env["results"]), 1)
        (self.tmp / "other" / ".claude").mkdir(parents=True)
        rc, out, _ = self.run_cli("list", "--project", str(self.tmp / "other"))
        self.assertIn("nothing disabled", out)
        rc, env = self.run_json("status")
        proj = [r for r in env["results"] if r.get("project")]
        self.assertEqual([(r["project"], r["parked"]) for r in proj], [(str(self.proj.resolve()), 1)])
        rc, env = self.run_json("cost", "--type", "skill")
        parked = [r for r in env["results"] if r["name"] == "demo-skill" and not r["enabled"]]
        self.assertEqual(parked, [])                      # a project item is not a user item


class ProjectBatchTest(ProjectCase):
    def batch(self, batch_id: str, *argv: str) -> tuple[int, dict]:
        with mock.patch.object(store, "BATCH", batch_id):
            return self.run_json(*argv)

    def test_undo_restores_the_project_item(self) -> None:
        before = file_bytes(self.proj)
        self.batch("b1", "disable", "skill", "demo-skill", "--project", str(self.proj))
        rc, env = self.run_json("undo")
        self.assertEqual(rc, 0, env)
        self.assertEqual(file_bytes(self.proj), before)
        self.assertEqual(self.state(), {})
        self.assertTrue((self.home / "skills" / "demo-skill").exists())
        rc, env = self.run_json("undo")                    # undo of undo re-parks in the project
        self.assertEqual(rc, 0, env)
        self.assertEqual(list(self.state()), [self.key])
        self.assertTrue((self.home / "skills" / "demo-skill").exists())

    def test_undo_mixed_batch_keeps_each_scope(self) -> None:
        with mock.patch.object(store, "BATCH", "b1"):
            self.run_json("disable", "skill", "demo-skill")
            self.run_json("disable", "skill", "demo-skill", "--project", str(self.proj))
        self.assertEqual(self.run_json("undo")[0], 0)
        self.assertEqual(self.state(), {})
        self.assertTrue(self.pskill.exists() and (self.home / "skills" / "demo-skill").exists())

    def test_undo_refuses_a_forged_home_project(self) -> None:
        with fs.lock():
            store.log("enable", "skill", "demo-skill", "ok", harness="claude", batch="b1",
                      project=str(self.tmp), scope="project")
        rc, env = self.run_json("undo")
        self.assertEqual(rc, 1)
        self.assertTrue((self.home / "skills" / "demo-skill").exists())
        self.assertEqual(self.state(), {})

    def test_enable_all_restores_both_scopes(self) -> None:
        self.run_json("disable", "skill", "demo-skill")
        self.p("disable", "skill", "demo-skill")
        self.p("disable", "agent", "demo-agent")
        rc, env = self.run_json("enable", "--all", "--project", str(self.proj))
        self.assertEqual(rc, 0, env)
        self.assertEqual(list(self.state()), ["claude:skill:demo-skill"])
        self.p("disable", "skill", "demo-skill")
        rc, env = self.run_json("enable", "--all")
        self.assertEqual(rc, 0, env)
        self.assertEqual(self.state(), {})
        self.assertTrue(self.pskill.exists() and (self.home / "skills" / "demo-skill").exists())

    def test_enable_all_with_a_vanished_project_is_an_error_row(self) -> None:
        self.p("disable", "skill", "demo-skill")
        shutil.rmtree(self.proj)
        rc, env = self.run_json("enable", "--all")
        self.assertEqual(rc, 1)
        self.assertEqual(list(self.state()), [self.key])

    def test_list_and_enable_all_refuse_a_missing_project_like_disable_does(self) -> None:
        gone = self.tmp / "work" / "gone"
        self.assertEqual(self.p("disable", "skill", "demo-skill", project=gone)[0], 4)
        before = snapshot(self.tmp)
        for argv in (("list",), ("enable", "--all")):
            rc, env = self.p(*argv, project=gone)
            self.assertEqual(rc, 4, (argv, env))
        self.assertEqual(snapshot(self.tmp), before)

    def test_profile_save_and_apply_in_project_scope(self) -> None:
        self.assertEqual(self.p("profile", "save", "proj")[0], 0)
        doc = json.loads((fs.profiles_dir() / "proj.json").read_text(encoding="utf-8"))
        self.assertIn({"harness": "claude", "type": "skill", "name": "demo-skill", "live": True},
                      doc["items"])
        self.assertNotIn("user copy", json.dumps(doc))
        self.p("disable", "skill", "demo-skill")
        self.run_json("disable", "skill", "demo-skill")                    # user scope
        rc, env = self.p("profile", "apply", "proj")
        self.assertEqual(rc, 0, env)
        self.assertTrue(self.pskill.exists())
        self.assertEqual(list(self.state()), ["claude:skill:demo-skill"])  # user entry untouched


def _other_filesystem_dir() -> Path | None:
    """A writable dir on a different device than the temp dir, if this machine has one."""
    base = os.stat(tempfile.gettempdir()).st_dev
    for cand in (os.environ.get("AGENT_TOGGLE_XFS_DIR"), "/dev/shm"):
        if cand and os.path.isdir(cand) and os.access(cand, os.W_OK) \
                and os.stat(cand).st_dev != base:
            return Path(cand)
    return None


class ProjectMcpTest(ProjectCase):
    """`--project` .mcp.json entries: remove_backup, a direct JSON edit, no claude CLI."""

    SERVERS = {"example-mcp": {"url": "https://example.com/mcp",
                               "headers": {"Authorization": "Bearer test-token-000"}},
               "other-mcp": {"command": "demo", "args": ["--é"]}}

    def setUp(self) -> None:
        super().setUp()
        self.mcpfile = self.proj / ".mcp.json"
        self.text = json.dumps({"mcpServers": self.SERVERS}, indent=2, ensure_ascii=False) + "\n"
        self.mcpfile.write_text(self.text, encoding="utf-8")
        self.mkey = store.make_key("claude", "mcp", "example-mcp", project=self.proj)

    def servers(self) -> dict:
        return json.loads(self.mcpfile.read_text(encoding="utf-8"))["mcpServers"]

    def test_round_trip_restores_the_entry_and_the_file_bytes(self) -> None:
        before = file_bytes(self.proj)
        rc, env = self.p("disable", "mcp", "example-mcp")
        self.assertEqual(rc, 0, env)
        self.assertEqual(self.cli_calls, [])                       # never shells out
        self.assertEqual(list(self.servers()), ["other-mcp"])
        entry = self.state()[self.mkey]
        self.assertEqual((entry["mechanism"], entry["project"]),
                         ("remove_backup", str(self.proj.resolve())))
        self.assertEqual(self.mkey, f"claude@{self.digest}:mcp:example-mcp")
        bp = Path(entry["backup"])
        self.assertTrue(bp.name.startswith(self.digest + "__"))
        self.assertTrue(bp.is_relative_to(fs.backup_dir()))
        self.assertIn(f"{self.mcpfile.resolve()} is a tracked change in git status",
                      " ".join(env["warnings"]))
        self.assertEqual(self.state().keys(), {self.mkey})
        rc, env = self.p("enable", "mcp", "example-mcp")
        self.assertEqual(rc, 0, env)
        self.assertEqual(file_bytes(self.proj), before)
        self.assertEqual(self.servers()["example-mcp"], self.SERVERS["example-mcp"])
        self.assertEqual(self.state(), {})
        self.assertEqual(self.cli_calls, [])

    def test_enable_merges_when_the_file_changed_meanwhile(self) -> None:
        self.p("disable", "mcp", "example-mcp")
        self.mcpfile.write_text(json.dumps({"mcpServers": {"other-mcp": {"x": 1}}, "k": 2}),
                                encoding="utf-8")
        self.assertEqual(self.p("enable", "mcp", "example-mcp")[0], 0)
        self.assertEqual(self.servers(), {"other-mcp": {"x": 1},
                                          "example-mcp": self.SERVERS["example-mcp"]})
        self.assertEqual(json.loads(self.mcpfile.read_text(encoding="utf-8"))["k"], 2)

    def test_enable_refuses_to_clobber_a_re_added_server(self) -> None:
        self.p("disable", "mcp", "example-mcp")
        self.mcpfile.write_text(self.text, encoding="utf-8")
        rc, env = self.p("enable", "mcp", "example-mcp")
        self.assertEqual(rc, 1)
        self.assertIn("already", env["results"][0]["detail"])
        self.assertEqual(self.mcpfile.read_text(encoding="utf-8"), self.text)
        self.assertIn(self.mkey, self.state())

    @unittest.skipIf(os.name == "nt", "modes")
    def test_backup_is_private_and_the_edit_keeps_the_file_mode(self) -> None:
        self.mcpfile.chmod(0o640)
        self.p("disable", "mcp", "example-mcp")
        bp = Path(self.state()[self.mkey]["backup"])
        self.assertEqual(bp.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.mcpfile.stat().st_mode & 0o777, 0o640)
        self.assertEqual(json.loads(bp.read_text(encoding="utf-8"))["project_json"]["entry"],
                         self.SERVERS["example-mcp"])

    def test_dry_run_writes_nothing(self) -> None:
        before = snapshot(self.tmp)
        rc, env = self.p("disable", "mcp", "example-mcp", "--dry-run")
        self.assertEqual((rc, env["results"][0]["status"]), (0, "planned"))
        self.assertEqual(snapshot(self.tmp), before)
        self.assertEqual(self.cli_calls, [])
        self.p("disable", "mcp", "example-mcp")
        before = snapshot(self.tmp)
        self.assertEqual(self.p("enable", "mcp", "example-mcp", "--dry-run")[0], 0)
        self.assertEqual(snapshot(self.tmp), before)

    def test_rollback_on_a_forced_verify_failure(self) -> None:
        with mock.patch.object(fs, "json_verify", lambda expected=None: self._boom):
            rc, env = self.p("disable", "mcp", "example-mcp")
        self.assertEqual(rc, 1)
        self.assertIn("rolled back", env["results"][0]["detail"])
        self.assertEqual(self.mcpfile.read_text(encoding="utf-8"), self.text)
        self.assertEqual(self.state(), {})
        self.assertEqual(list(fs.backup_dir().glob("*")), [])      # no orphan backup

    @staticmethod
    def _boom(before: str, after: str) -> str:
        raise ValueError("forced")

    def test_non_strict_json_is_refused(self) -> None:
        for bad in ('{"mcpServers": {"example-mcp": {},}}', '// c\n{"mcpServers": {}}',
                    '\ufeff{"mcpServers": {"example-mcp": {}}}',
                    '{"mcpServers": {"example-mcp": {}, "example-mcp": {}}}',
                    '{"servers": {}}', '[]'):
            self.mcpfile.write_text(bad, encoding="utf-8")
            rc, env = self.p("disable", "mcp", "example-mcp")
            self.assertEqual(rc, 1, bad)
            self.assertEqual(self.mcpfile.read_text(encoding="utf-8"), bad)
        self.assertEqual(self.state(), {})

    def test_missing_file_or_server_is_an_error_row(self) -> None:
        self.assertEqual(self.p("disable", "mcp", "nope-mcp")[0], 1)
        self.mcpfile.unlink()
        rc, env = self.p("disable", "mcp", "example-mcp")
        self.assertEqual(rc, 1)
        self.assertIn("not found", env["results"][0]["detail"])

    @unittest.skipUnless(CAN_SYMLINK, "cannot create symlinks here")
    def test_symlinked_mcp_json_is_never_written_through(self) -> None:
        target = self.tmp / "elsewhere.json"
        target.write_text(self.text, encoding="utf-8")
        self.mcpfile.unlink()
        self.mcpfile.symlink_to(target)
        rc, env = self.p("disable", "mcp", "example-mcp")
        self.assertEqual(rc, 1)
        self.assertIn("symlink", env["results"][0]["detail"])
        self.assertEqual(target.read_text(encoding="utf-8"), self.text)

    def test_user_scope_mcp_state_is_never_touched(self) -> None:
        self.claude_json({"mcpServers": {"example-mcp": {"url": "https://user.example.com"}}})
        self.cli_rc = 0
        self.assertEqual(self.run_json("disable", "mcp", "example-mcp")[0], 0)
        user_entry = self.state()["claude:mcp:example-mcp"]
        n_calls = len(self.cli_calls)
        self.assertEqual(self.p("disable", "mcp", "example-mcp")[0], 0)
        self.assertEqual(self.state()["claude:mcp:example-mcp"], user_entry)
        self.assertEqual(self.p("enable", "mcp", "example-mcp")[0], 0)
        self.assertEqual(self.state().keys(), {"claude:mcp:example-mcp"})
        self.assertEqual(len(self.cli_calls), n_calls)             # project ops: no CLI
        self.assertEqual(list(self.servers()), ["example-mcp", "other-mcp"])
        self.assertEqual(self.p("enable", "mcp", "example-mcp")[0], 1)  # user entry unreachable

    def test_tampered_entries_are_refused(self) -> None:
        self.p("disable", "mcp", "example-mcp")
        state = json.loads(fs.state_file().read_text(encoding="utf-8"))
        entry = state["disabled"][self.mkey]
        for field, value in (("backup", str(self.proj / "evil.json")),
                             ("backup", str(fs.backup_dir() / "claude__other.json")),
                             ("project", str(self.tmp / "work"))):
            tampered = {**state, "disabled": {self.mkey: {**entry, field: value}}}
            fs.state_file().write_text(json.dumps(tampered), encoding="utf-8")
            rc, env = self.p("enable", "mcp", "example-mcp")
            self.assertEqual(rc, 1, (field, value))
            self.assertIn("refused", env["results"][0]["detail"])
        self.assertEqual(list(self.servers()), ["other-mcp"])

    def test_undo_enable_all_and_profile_cover_project_mcp(self) -> None:
        before = file_bytes(self.proj)
        with mock.patch.object(store, "BATCH", "b1"):
            self.run_json("disable", "mcp", "example-mcp", "--project", str(self.proj))
        self.assertEqual(self.run_json("undo")[0], 0)
        self.assertEqual(file_bytes(self.proj), before)
        self.assertEqual(self.run_json("undo")[0], 0)              # undo of undo re-parks
        self.assertEqual(list(self.state()), [self.mkey])
        rc, env = self.run_json("enable", "--all")
        self.assertEqual(rc, 0, env)
        self.assertEqual(file_bytes(self.proj), before)
        self.assertEqual(self.p("profile", "save", "pm")[0], 0)
        doc = json.loads((fs.profiles_dir() / "pm.json").read_text(encoding="utf-8"))
        self.assertIn({"harness": "claude", "type": "mcp", "name": "example-mcp", "live": True},
                      doc["items"])
        self.p("disable", "mcp", "example-mcp")
        self.assertEqual(self.p("profile", "apply", "pm")[0], 0)
        self.assertEqual(file_bytes(self.proj), before)

    def test_a_project_entry_cannot_be_replayed_under_a_user_key(self) -> None:
        self.p("disable", "mcp", "example-mcp")
        state = json.loads(fs.state_file().read_text(encoding="utf-8"))
        entry = state["disabled"].pop(self.mkey)
        del entry["project"]
        state["disabled"]["claude:mcp:example-mcp"] = entry
        fs.state_file().write_text(json.dumps(state), encoding="utf-8")
        self.cli_rc = 0
        rc, env = self.run_json("enable", "mcp", "example-mcp")
        self.assertEqual(rc, 1)
        self.assertIn("refused", env["results"][0]["detail"])
        self.assertEqual(self.cli_calls, [])

    def test_hostile_names_never_reach_profiles(self) -> None:
        self.mcpfile.write_text(json.dumps({"mcpServers": {"a/b": {}, "-x": {}, "ok-mcp": {}}}),
                                encoding="utf-8")
        self.assertEqual(self.p("profile", "save", "h")[0], 0)
        doc = json.loads((fs.profiles_dir() / "h.json").read_text(encoding="utf-8"))
        self.assertEqual([i["name"] for i in doc["items"] if i["type"] == "mcp"], ["ok-mcp"])

    def test_a_file_edited_between_plan_and_write_is_not_overwritten(self) -> None:
        from agent_toggle.backends import mcp_json
        raw, before, after = mcp_json.project_mcp_remove(self.mcpfile, "example-mcp")
        self.mcpfile.write_text('{"mcpServers": {"edited": {}}}', encoding="utf-8")
        with self.assertRaises(fs.WriteError):
            mcp_json.write_project_mcp(self.mcpfile, after, before)
        self.assertEqual(self.mcpfile.read_text(encoding="utf-8"), '{"mcpServers": {"edited": {}}}')

    def test_project_with_only_an_mcp_json_is_usable(self) -> None:
        bare = self.tmp / "bare"
        bare.mkdir()
        (bare / ".mcp.json").write_text(self.text, encoding="utf-8")
        self.assertEqual(self.p("disable", "mcp", "example-mcp", project=bare)[0], 0)
        self.assertEqual(self.p("enable", "mcp", "example-mcp", project=bare)[0], 0)
        self.assertEqual((bare / ".mcp.json").read_text(encoding="utf-8"), self.text)


class CrossFilesystemTest(ProjectCase):
    """shutil.move across devices is copy+delete (see fs.safe_move's ceiling note)."""

    def test_round_trip_across_filesystems(self) -> None:
        other = _other_filesystem_dir()
        if other is None:
            self.skipTest("no second writable filesystem (set AGENT_TOGGLE_XFS_DIR to one)")
        proj = Path(tempfile.mkdtemp(prefix="agent-toggle-xfs-", dir=other))
        self.addCleanup(shutil.rmtree, proj, ignore_errors=True)
        shutil.copytree(self.pclaude, proj / ".claude")
        before = file_bytes(proj)
        self.assertEqual(self.p("disable", "skill", "demo-skill", project=proj)[0], 0)
        self.assertFalse((proj / ".claude/skills/demo-skill").exists())
        self.assertEqual(self.p("enable", "skill", "demo-skill", project=proj)[0], 0)
        self.assertEqual(file_bytes(proj), before)


if __name__ == "__main__":
    unittest.main()
