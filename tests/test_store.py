"""Legacy ~/.claude-toggle import."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from base import CAN_SYMLINK, SandboxCase
from test_cli_surface import CliCase

from agent_toggle import fs, harnesses, store


class MigrateTest(SandboxCase):
    def test_migrate_imports_legacy_state(self) -> None:
        legacy = fs.legacy_state_dir()
        (legacy / "mcp-backups").mkdir(parents=True)
        backup = legacy / "mcp-backups/example-mcp.json"
        backup.write_text('{"type": "http", "url": "http://127.0.0.1:8765/mcp"}', encoding="utf-8")
        (legacy / "state.json").write_text(json.dumps({"version": 1, "disabled": {
            "mcp:example-mcp": {"type": "mcp", "name": "example-mcp",
                                "backup": str(backup), "at": "2026-09-19T00:00:00+0800"},
        }}), encoding="utf-8")
        state = {"version": 2, "disabled": {}}
        store.migrate(state)

        entry = state["disabled"].get("claude:mcp:example-mcp")
        # legacy entry imported under a harness key
        self.assertIsNotNone(entry)
        # imported entry is tagged claude
        self.assertEqual(entry["harness"], "claude")
        # backup copied into the new store
        self.assertTrue(Path(entry["backup"]).is_file())
        # legacy backup left in place
        self.assertTrue(backup.is_file())


class UserParkLayoutTest(CliCase):
    """User scope parks under parked/user/<owner>/<sub>/<name>, never in a harness home."""

    def test_disable_parks_centrally_and_enable_restores(self) -> None:
        self.write("skills/demo-skill/SKILL.md", "demo")
        before = sorted(p.name for p in self.home.iterdir())
        self.assertEqual(self.run_cli("disable", "skill", "demo-skill")[0], 0)
        parked = self.user_parked("skills", "demo-skill")
        self.assertEqual(parked, fs.parked_dir() / "user" / "claude" / "skills" / "demo-skill")
        self.assertEqual((parked / "SKILL.md").read_text(encoding="utf-8"), "demo")
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), before)  # home untouched
        entry = store.load_state()["disabled"]["claude:skill:demo-skill"]
        self.assertEqual(entry["parked_at"], str(parked))
        self.assertEqual(self.run_cli("enable", "skill", "demo-skill")[0], 0)
        self.assertEqual((self.home / "skills/demo-skill/SKILL.md").read_text(encoding="utf-8"),
                         "demo")
        self.assertFalse(parked.exists())
        self.assertEqual(store.load_state()["disabled"], {})

    def test_an_absolute_sub_parks_under_ext_sha8(self) -> None:
        ext = self.tmp / "ext-skills"
        (ext / "x").mkdir(parents=True)
        (ext / "x" / "SKILL.md").write_text("x", encoding="utf-8")
        oc = self.tmp / ".config" / "opencode"
        oc.mkdir(parents=True)
        (oc / "opencode.json").write_text(json.dumps({"skills": {"paths": [str(ext)]}}),
                                          encoding="utf-8")
        self.assertEqual(self.run_cli("disable", "skill", "x", "--harness", "opencode")[0], 0)
        sha8 = hashlib.sha1(str(ext.resolve()).encode("utf-8")).hexdigest()[:8]
        parked = fs.parked_dir() / "user" / "opencode" / f"ext-{sha8}" / "x"
        self.assertTrue((parked / "SKILL.md").is_file())
        self.assertEqual(sorted(p.name for p in ext.iterdir()), [])     # nothing beside it
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir() if "disabled" in p.name), [])
        self.assertEqual(self.run_cli("enable", "skill", "x", "--harness", "opencode")[0], 0)
        self.assertTrue((ext / "x" / "SKILL.md").is_file())


class MigrateParksTest(CliCase):
    """`migrate` moves legacy `<live>-disabled` dirs into parked/user/<owner>/<sub>."""

    def save(self, entries: dict) -> None:
        fs.private_dir(fs.state_dir())
        fs.state_file().write_text(json.dumps({"version": 3, "disabled": entries}),
                                   encoding="utf-8")

    def entry(self, type_: str, name: str, origin: str, parked: str) -> dict:
        return {"mechanism": "move", "harness": "claude", "type": type_, "name": name,
                "origin": str(self.home / origin), "parked_at": str(self.home / parked),
                "companions": [], "at": "2026-01-01T00:00:00+0000"}

    def setUp(self) -> None:
        super().setUp()
        self.write("skills-disabled/demo-skill/SKILL.md", "demo")
        self.write("skills-disabled/stray/SKILL.md")              # no state entry: moves too
        self.write("skills-disabled/clash/SKILL.md", "legacy")
        self.write("commands-disabled/orch/batch.md", "batch")
        self.write_parked("skills/clash/SKILL.md", "new")           # parked since the upgrade
        self.write_parked("commands/orch/other.md")                 # a group to merge into
        self.save({
            "claude:skill:demo-skill": self.entry("skill", "demo-skill", "skills/demo-skill",
                                                  "skills-disabled/demo-skill"),
            "claude:command:orch:batch": self.entry("command", "orch:batch",
                                                    "commands/orch/batch.md",
                                                    "commands-disabled/orch/batch.md")})

    def test_enable_before_migrate_names_the_fix(self) -> None:
        rc, env = self.run_json("enable", "skill", "demo-skill")
        self.assertEqual(rc, 1)
        self.assertIn("agent-toggle migrate", env["results"][0]["detail"])
        rc, out, _ = self.run_cli("status")
        self.assertIn(f"{self.home / 'skills-disabled'} is a legacy park dir -- run: "
                      f"agent-toggle migrate", out)

    def test_moves_repoints_refuses_a_clash_and_is_idempotent(self) -> None:
        rc, env = self.run_json("migrate")
        self.assertEqual(rc, 1)                                     # the clash is refused
        detail = env["results"][0]["detail"]
        self.assertIn(f"refused: {self.user_parked('skills', 'clash')} already exists", detail)
        state = store.load_state()["disabled"]
        self.assertEqual(state["claude:skill:demo-skill"]["parked_at"],
                         str(self.user_parked("skills", "demo-skill")))
        self.assertEqual(state["claude:command:orch:batch"]["parked_at"],
                         str(self.user_parked("commands", "orch", "batch.md")))
        for parts, text in ((("skills", "demo-skill", "SKILL.md"), "demo"),
                            (("skills", "clash", "SKILL.md"), "new"),
                            (("commands", "orch", "batch.md"), "batch")):
            self.assertEqual(self.user_parked(*parts).read_text(encoding="utf-8"), text)
        self.assertTrue(self.user_parked("skills", "stray", "SKILL.md").is_file())
        self.assertTrue(self.user_parked("commands", "orch", "other.md").is_file())
        self.assertFalse((self.home / "commands-disabled").exists())   # emptied: removed
        self.assertEqual([p.name for p in (self.home / "skills-disabled").iterdir()], ["clash"])
        self.assertEqual(self.run_cli("enable", "skill", "demo-skill")[0], 0)
        self.assertEqual((self.home / "skills/demo-skill/SKILL.md").read_text(encoding="utf-8"),
                         "demo")
        # the user resolves the clash; the next run finishes, the one after changes nothing
        (self.home / "skills-disabled/clash/SKILL.md").unlink()
        (self.home / "skills-disabled/clash").rmdir()
        rc, env = self.run_json("migrate")
        self.assertEqual(rc, 0, env)
        self.assertFalse((self.home / "skills-disabled").exists())
        before = fs.state_file().read_bytes()
        rc, out, _ = self.run_cli("migrate")
        self.assertEqual(rc, 0)
        self.assertIn("park dirs: nothing to migrate", out)
        self.assertEqual(fs.state_file().read_bytes(), before)

    def test_a_killed_run_is_repointed_by_the_next(self) -> None:
        # the item moved but the state save never happened
        self.user_parked("skills").mkdir(parents=True, exist_ok=True)
        (self.home / "skills-disabled/demo-skill").rename(self.user_parked("skills", "demo-skill"))
        self.assertEqual(self.run_cli("migrate")[0], 1)            # the clash still refuses
        self.assertEqual(store.load_state()["disabled"]["claude:skill:demo-skill"]["parked_at"],
                         str(self.user_parked("skills", "demo-skill")))

    @unittest.skipUnless(CAN_SYMLINK, "cannot create symlinks here")
    def test_a_link_left_dangling_by_0_7_0_is_re_aimed(self) -> None:
        real = self.write(".repos/r/linked/SKILL.md", "hi").parent
        link = self.user_parked("skills", "linked")
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to("../.repos/r/linked")                       # 0.7.0 kept the text
        gone = self.user_parked("skills", "gone")
        gone.symlink_to("../.repos/nowhere")                        # dangles from skills/ too
        self.run_json("migrate")
        self.assertEqual(link.resolve(), real.resolve())
        self.assertEqual(os.readlink(gone), "../.repos/nowhere")    # left alone
        rc, env = self.run_json("migrate")
        self.assertNotIn("re-aimed", env["results"][0]["detail"])   # idempotent


class LogSchemaTest(SandboxCase):
    FIELDS = {"ts", "harness", "type", "name", "action", "result", "batch",
              "project", "scope", "detail"}

    def rows(self) -> list[dict]:
        return [json.loads(line) for line in
                fs.log_file().read_text(encoding="utf-8").splitlines()]

    def test_every_row_carries_the_full_schema(self) -> None:
        store.log("disable", "skill", "demo-skill", "ok")
        store.log("enable", "mcp", "example-mcp", "ok", "detail text", harness="codex",
                  project="/proj", scope="local")
        a, b = self.rows()
        self.assertEqual(set(a), self.FIELDS)
        self.assertEqual(set(b), self.FIELDS)
        self.assertEqual(tuple(a), store.LOG_FIELDS)
        self.assertEqual((a["project"], a["scope"], a["detail"]), (None, "user", ""))
        self.assertEqual((b["harness"], b["project"], b["scope"], b["detail"]),
                         ("codex", "/proj", "local", "detail text"))

    def test_batch_is_one_id_per_process(self) -> None:
        store.log("disable", "skill", "a", "ok")
        store.log("disable", "skill", "b", "ok")
        a, b = self.rows()
        self.assertEqual(a["batch"], b["batch"])
        self.assertTrue(a["batch"].endswith(f"-{os.getpid()}"), a["batch"])


class BadStateTest(CliCase):
    """A hand-edited state file gives a named error or a row, never a TypeError/KeyError."""

    def save(self, disabled: dict) -> None:
        fs.private_dir(fs.state_dir())
        fs.state_file().write_text(json.dumps({"version": 3, "disabled": disabled}),
                                   encoding="utf-8")

    def test_a_non_object_entry_names_its_key(self) -> None:
        self.save({"claude:skill:x": 5})
        for argv in (("list",), ("status",), ("cost",), ("enable", "--all"),
                     ("profile", "save", "p")):
            rc, env = self.run_json(*argv)
            self.assertEqual(rc, 1, argv)
            self.assertIn("claude:skill:x", json.dumps(env), argv)
            self.assertNotIn("TypeError", json.dumps(env), argv)

    def test_entries_with_bad_field_types_still_list(self) -> None:
        self.save({"claude:skill:x": {"mechanism": "move", "harness": "claude", "type": "skill",
                                      "name": "x", "shared_with": 5, "companions": 3, "at": 7},
                   "claude:skill:y": {"mechanism": "move", "harness": "claude", "type": "skill",
                                      "name": "y"}})
        for argv in (("list",), ("status",), ("cost",), ("profile", "save", "p")):
            rc, env = self.run_json(*argv)
            self.assertEqual(rc, 0, (argv, env))
        rc, env = self.run_json("list")
        self.assertEqual(sorted(r["name"] for r in env["results"]), ["x", "y"])


class KeyTest(unittest.TestCase):
    def test_user_scope_key_round_trips(self) -> None:
        key = store.make_key("claude", "skill", "orch:batch")
        self.assertEqual(key, "claude:skill:orch:batch")
        self.assertEqual(store.parse_key(key), ("claude", None, "skill", "orch:batch"))

    def test_project_scope_key_round_trips(self) -> None:
        proj = Path(tempfile.gettempdir())
        h = hashlib.sha1(str(proj.resolve()).encode("utf-8")).hexdigest()[:8]
        key = store.make_key("claude", "mcp", "example-mcp", project=proj)
        self.assertEqual(key, f"claude@{h}:mcp:example-mcp")
        self.assertEqual(store.parse_key(key), ("claude", h, "mcp", "example-mcp"))

    def test_malformed_key_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            store.parse_key("claude:skill")
        for bad in ("claude@:skill:x", "claude@XYZ:skill:x", "@0123abcd:skill:x"):
            with self.assertRaises(ValueError):
                store.parse_key(bad)


class MechanismOfTest(unittest.TestCase):
    def test_connector_and_flag_stay_distinct(self) -> None:
        self.assertEqual(store.mechanism_of({"connector": True}), "connector")
        self.assertEqual(store.mechanism_of(
            {"flag": {"file": "settings.json", "pointer": "/x", "was": True}}), "flag")

    def test_upgrade_still_stores_flag_for_connectors(self) -> None:
        state = {"version": 2, "disabled": {"claude:mcp:demo": {"connector": True}}}
        store.upgrade(state)
        self.assertEqual(state["disabled"]["claude:mcp:demo"]["mechanism"], "flag")


class CheckEntryTest(SandboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.table = harnesses.build(self.tmp)
        self.proj = self.tmp / "proj"
        self.park = fs.parked_dir() / store.project_digest(self.proj)

    def entry(self, **kw) -> dict:
        return {"harness": "claude", "type": "skill", "name": "demo-skill", **kw}

    def assertOk(self, entry: dict) -> None:
        self.assertIsNone(store.check_entry(entry, self.table), entry)

    def assertRefused(self, entry: dict, field: str) -> None:
        reason = store.check_entry(entry, self.table)
        self.assertIsNotNone(reason, entry)
        self.assertIn(field, reason)

    def test_legit_entries_pass(self) -> None:
        self.assertOk(self.entry(mechanism="move", origin=str(self.home / "skills/demo-skill"),
                                 parked_at=str(self.user_parked("skills", "demo-skill"))))
        self.assertOk(self.entry(mechanism="move", name="orch:batch",
                                 origin=str(self.home / "commands/orch/batch.md"),
                                 parked_at=str(self.user_parked("commands", "orch/batch.md"))))
        self.assertOk(self.entry(mechanism="move", project=str(self.proj),
                                 origin=str(self.proj / ".claude/skills/demo-skill"),
                                 parked_at=str(self.park / "skills-disabled/demo-skill")))
        self.assertOk(self.entry(type="mcp", name="example-mcp", mechanism="remove_backup",
                                 backup=str(fs.backup_dir() / "claude__example-mcp.json"),
                                 scope="user", project=None))
        self.assertOk(self.entry(type="mcp", name="example-mcp", mechanism="flag",
                                 connector=True))
        self.assertOk(self.entry(mechanism="flag", flag={"file": str(self.home / "settings.json"),
                                                         "pointer": "/x", "was": True}))

    def test_tampered_origin(self) -> None:
        self.assertRefused(self.entry(origin=str(self.tmp / "elsewhere/demo-skill"),
                                      parked_at=str(self.user_parked("skills", "demo-skill"))),
                           "origin")
        # another harness's home is not this entry's home
        self.assertRefused(self.entry(origin=str(self.tmp / ".codex/skills/demo-skill"),
                                      parked_at=str(self.user_parked("skills", "demo-skill"))),
                           "origin")

    def test_tampered_parked_at(self) -> None:
        good_origin = str(self.home / "skills/demo-skill")
        for bad in (self.tmp / "elsewhere/demo-skill",
                    self.home / "skills/demo-skill",          # the live dir
                    self.home / "skills-disabled/demo-skill", # a legacy park dir
                    fs.state_dir() / "demo-skill",
                    fs.parked_dir() / "demo-skill",           # the park root itself
                    fs.parked_dir() / "user/claude/other/demo-skill",       # undeclared sub
                    self.user_parked("skills", "demo-skill", owner="codex")):  # not its owner
            self.assertRefused(self.entry(origin=good_origin, parked_at=str(bad)), "parked_at")
        legacy = self.entry(origin=good_origin,
                            parked_at=str(self.home / "skills-disabled/demo-skill"))
        self.assertIn("agent-toggle migrate", store.check_entry(legacy, self.table))

    @unittest.skipUnless(CAN_SYMLINK, "cannot create symlinks here")
    def test_user_park_through_a_symlinked_parent_is_refused(self) -> None:
        real = self.tmp / "elsewhere"
        real.mkdir()
        self.user_parked("skills").parent.mkdir(parents=True)
        self.user_parked("skills").symlink_to(real)
        self.assertRefused(self.entry(origin=str(self.home / "skills/demo-skill"),
                                      parked_at=str(self.user_parked("skills", "demo-skill"))),
                           "parked_at")

    def test_absolute_sub_parks_under_ext_sha8(self) -> None:
        ext = self.tmp / "ext-skills"
        oc = self.tmp / ".config" / "opencode"
        oc.mkdir(parents=True)
        (oc / "opencode.json").write_text(json.dumps({"skills": {"paths": [str(ext)]}}),
                                          encoding="utf-8")
        sha8 = hashlib.sha1(str(ext.resolve()).encode("utf-8")).hexdigest()[:8]
        park = fs.parked_dir() / "user" / "opencode" / f"ext-{sha8}"
        self.assertEqual(fs.user_park("opencode", str(ext)), park)
        entry = self.entry(harness="opencode", origin=str(ext / "x"), parked_at=str(park / "x"))
        self.assertIsNone(store.check_entry(entry, harnesses.build(self.tmp)))

    def test_forged_project(self) -> None:
        for proj in (Path(self.tmp.anchor), self.tmp, self.tmp.parent):   # root, $HOME, above
            self.assertRefused(self.entry(project=str(proj), origin=str(proj / "x"),
                                          parked_at=str(fs.parked_dir() / "x")), "project")
        legit = self.entry(project=str(self.proj), origin=str(self.proj / ".claude/skills/x"),
                           parked_at=str(self.park / "x"))
        key = store.make_key("claude", "skill", "x", project=self.proj)
        self.assertIsNone(store.check_entry(legit, self.table, key))
        self.assertIn("project", store.check_entry(legit, self.table, "claude:skill:x"))
        other = store.make_key("claude", "skill", "x", project=self.tmp / "other")
        self.assertIn("project", store.check_entry(legit, self.table, other))

    def test_opencode_project_dir(self) -> None:
        self.assertOk(self.entry(harness="opencode", project=str(self.proj),
                                 origin=str(self.proj / ".opencode/skills/x"),
                                 parked_at=str(self.park / "skills-disabled/x")))

    def test_project_entries_park_only_under_their_parked_dir(self) -> None:
        origin = str(self.proj / ".claude/skills/demo-skill")
        other = fs.parked_dir() / store.project_digest(self.tmp / "other")
        for bad in (self.proj / ".claude/skills-disabled/demo-skill",   # inside the repo
                    self.user_parked("skills", "demo-skill"),          # user scope park
                    other / "skills-disabled/demo-skill",               # another project's
                    Path(str(self.park) + "0") / "demo-skill",          # string prefix
                    fs.parked_dir() / "demo-skill"):
            self.assertRefused(self.entry(project=str(self.proj), origin=origin,
                                          parked_at=str(bad)), "parked_at")
        for bad in (self.home / "skills/demo-skill", self.proj / "src/demo-skill"):
            self.assertRefused(self.entry(project=str(self.proj), origin=str(bad),
                                          parked_at=str(self.park / "x")), "origin")

    @unittest.skipUnless(CAN_SYMLINK, "cannot create symlinks here")
    def test_project_park_through_a_symlinked_parent_is_refused(self) -> None:
        real = self.tmp / "elsewhere"
        real.mkdir()
        fs.parked_dir().mkdir(parents=True)
        self.park.symlink_to(real)
        self.assertRefused(self.entry(project=str(self.proj),
                                      origin=str(self.proj / ".claude/skills/demo-skill"),
                                      parked_at=str(self.park / "skills-disabled/demo-skill")),
                           "parked_at")

    def test_flag_file_outside_home(self) -> None:
        self.assertRefused(self.entry(mechanism="flag",
                                      flag={"file": str(self.tmp / "elsewhere.json")}), "flag")
        self.assertOk(self.entry(mechanism="flag", flag={"file": str(fs.claude_json())}))

    def test_malformed_entries_are_refused_not_raised(self) -> None:
        self.assertRefused(["not", "a", "dict"], "object")       # type: ignore[arg-type]
        self.assertRefused({"harness": ["claude"]}, "harness")

    def test_tampered_backup(self) -> None:
        self.assertRefused(self.entry(type="mcp", mechanism="remove_backup",
                                      backup=str(self.tmp / "elsewhere.json")), "backup")

    def test_dotdot_in_any_path_field(self) -> None:
        dd = str(self.home / "skills" / ".." / "skills" / "demo-skill")
        self.assertRefused(self.entry(origin=dd,
                                      parked_at=str(self.user_parked("skills", "demo-skill"))),
                           "origin")
        self.assertRefused(self.entry(type="mcp", mechanism="remove_backup",
                                      backup=str(fs.backup_dir() / ".." / "x.json")), "backup")
        self.assertRefused(self.entry(mechanism="flag",
                                      flag={"file": str(self.home / ".." / "x.json")}), "flag")

    def test_unknown_harness_and_non_string_paths(self) -> None:
        self.assertRefused({"harness": "nope", "type": "skill", "name": "x"}, "harness")
        self.assertRefused(self.entry(origin=["not", "a", "path"]), "origin")
        self.assertRefused(self.entry(origin="skills/demo-skill"), "origin")
