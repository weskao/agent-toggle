"""doctor: read-only drift + state health check (DESIGN s6 "Harness drift")."""
from __future__ import annotations

import json
import os
import shutil
import unittest
from pathlib import Path
from unittest import mock

from base import CAN_SYMLINK
from test_cli_surface import CliCase, snapshot
from test_conformance import FIXTURES

from agent_toggle import doctor, fs, store
from agent_toggle.output import Result

HAS_TOMLLIB = fs._tomllib() is not None


class DoctorCase(CliCase):
    def setUp(self) -> None:
        super().setUp()
        self.cli_rc = 0
        for h in ("claude", "codex", "openclaw", "opencode"):
            shutil.copytree(FIXTURES / h, self.tmp, dirs_exist_ok=True)
        self.oc = self.tmp / ".config" / "opencode" / "opencode.json"
        self.claw = self.tmp / ".openclaw" / "openclaw.json"

    def doctor(self, *argv: str) -> tuple[int, list[dict]]:
        rc, env = self.run_json("doctor", *argv)
        self.assertEqual(env["command"], "doctor")
        self.assertEqual(env["ok"], rc == 0)
        return rc, env["results"]

    def rows(self, results: list[dict], status: str, **match) -> list[dict]:
        return [r for r in results if r["status"] == status
                and all(r.get(k) == v for k, v in match.items())]

    def state(self) -> dict:
        return json.loads(fs.state_file().read_text(encoding="utf-8"))["disabled"]


class LayoutTest(DoctorCase):
    def test_clean_homes_exit_zero(self) -> None:
        rc, rows = self.doctor()
        self.assertEqual(rc, 0, rows)
        self.assertEqual(self.rows(rows, "error"), [])
        self.assertEqual({r["harness"] for r in self.rows(rows, "ok")},
                         {"claude", "codex", "openclaw", "opencode"})
        self.assertTrue(all(r["action"] == "doctor" for r in rows))

    def test_harness_filter(self) -> None:
        rc, rows = self.doctor("--harness", "codex")
        self.assertEqual(rc, 0)
        self.assertEqual({r["harness"] for r in rows}, {"codex"})

    def test_renamed_dir_is_absent_not_a_failure(self) -> None:
        (self.home / "skills").rename(self.home / "skills-renamed")
        rc, rows = self.doctor("--harness", "claude")
        self.assertEqual(rc, 0, rows)
        self.assertEqual(len(self.rows(rows, "absent", harness="claude", type="skill")), 1)

    def test_park_dir_alone_counts_as_present(self) -> None:
        (self.home / "skills").rename(self.home / "skills-disabled")
        rc, rows = self.doctor("--harness", "claude")
        self.assertEqual(self.rows(rows, "absent", type="skill"), [])

    @unittest.skipUnless(HAS_TOMLLIB, "needs tomllib")
    def test_truncated_toml_is_layout_changed(self) -> None:
        (self.tmp / ".codex" / "config.toml").write_text('[mcp_servers.example-mcp\ncommand = "ex',
                                                         encoding="utf-8")
        rc, rows = self.doctor()
        self.assertEqual(rc, 1)
        (bad,) = self.rows(rows, "error", harness="codex", type="mcp")
        self.assertIn("layout changed", bad["detail"])

    def test_missing_mcp_key_is_absent_not_a_failure(self) -> None:
        (self.tmp / ".claude.json").write_text('{"theme": "dark"}', encoding="utf-8")
        rc, rows = self.doctor()
        self.assertEqual(rc, 0, rows)
        (note,) = self.rows(rows, "absent", harness="claude", type="mcp")
        self.assertIn("mcpServers", note["detail"])

    def test_missing_mcp_files_are_absent_not_a_failure(self) -> None:
        (self.tmp / ".claude.json").unlink(missing_ok=True)
        (self.tmp / ".codex" / "config.toml").unlink()
        rc, rows = self.doctor()
        self.assertEqual(rc, 0, rows)
        self.assertEqual({r["harness"] for r in self.rows(rows, "absent", type="mcp")},
                         {"claude", "codex"})

    def test_toml_without_tomllib_is_parsed_by_the_fallback(self) -> None:
        cfg = self.tmp / ".codex" / "config.toml"
        with mock.patch.object(fs, "_tomllib", lambda: None):
            rc, rows = self.doctor("--harness", "codex")
            self.assertEqual(rc, 0, rows)
            self.assertFalse([r for r in rows if r["status"] == "unverified" and r["type"] == "mcp"])
            self.assertFalse(self.rows(rows, "absent", harness="codex", type="mcp"))
            cfg.write_text("[mcp_servers.x]\n[mcp_servers.x]\n", encoding="utf-8")
            rc, rows = self.doctor("--harness", "codex")
            self.assertEqual(rc, 1)
            self.assertIn("does not parse", self.rows(rows, "error", harness="codex", type="mcp")[0]["detail"])
            cfg.write_text("[other]\nx = 1\n", encoding="utf-8")
            self.assertEqual(len(self.rows(self.doctor("--harness", "codex")[1], "absent",
                                           harness="codex", type="mcp")), 1)
            cfg.unlink()
            rc, rows = self.doctor("--harness", "codex")
        self.assertEqual(rc, 0, rows)
        self.assertEqual(len(self.rows(rows, "absent", harness="codex", type="mcp")), 1)

    def test_broken_openclaw_json_is_layout_changed(self) -> None:
        self.claw.write_text('{"skills": {"entries": ', encoding="utf-8")
        rc, rows = self.doctor()
        self.assertEqual(rc, 1)
        bad = self.rows(rows, "error", harness="openclaw")
        self.assertTrue(bad and all("layout changed" in r["detail"] for r in bad), rows)

    def test_missing_flag_prefix_is_absent_not_a_failure(self) -> None:
        self.oc.write_text('{"servers": {}}', encoding="utf-8")
        rc, rows = self.doctor()
        self.assertEqual(rc, 0, rows)
        (note,) = self.rows(rows, "absent", harness="opencode", type="mcp")
        self.assertIn("mcp", note["detail"])

    def test_missing_flag_file_is_absent_not_a_failure(self) -> None:
        self.claw.unlink()
        rc, rows = self.doctor("--harness", "openclaw")
        self.assertEqual(rc, 0, rows)
        self.assertTrue(self.rows(rows, "absent", harness="openclaw"))

    def test_recorded_flag_whose_file_vanished_stays_an_error(self) -> None:
        self.assertEqual(self.run_cli("disable", "skill", "demo-skill", "--harness", "openclaw")[0], 0)
        self.claw.unlink()
        rc, rows = self.doctor("--harness", "openclaw")
        self.assertEqual(rc, 1)
        self.assertTrue(self.rows(rows, "error", harness="openclaw", type="skill", name="demo-skill"))

    def test_recorded_flag_whose_pointer_vanished_stays_an_error(self) -> None:
        self.assertEqual(self.run_cli("disable", "skill", "demo-skill", "--harness", "openclaw")[0], 0)
        self.claw.write_text('{"skills": {"entries": {}}}', encoding="utf-8")
        rc, rows = self.doctor("--harness", "openclaw")
        self.assertEqual(rc, 1)
        self.assertTrue(self.rows(rows, "error", harness="openclaw", type="skill", name="demo-skill"))

    def test_one_row_per_broken_shared_flag_file(self) -> None:
        self.claw.write_text('{"skills": ', encoding="utf-8")
        rows = self.rows(self.doctor("--harness", "openclaw")[1], "error")
        self.assertEqual(len(rows), 1, rows)

    def test_jsonc_claude_json_is_an_error(self) -> None:
        (self.tmp / ".claude.json").write_text('{"mcpServers": {},}', encoding="utf-8")
        self.assertEqual(self.doctor("--harness", "claude")[0], 1)

    def test_jsonc_is_a_note_not_corruption(self) -> None:
        self.oc.write_text('{\n  // comment\n  "mcp": {"example-mcp": {"enabled": true,},},\n}\n',
                           encoding="utf-8")
        rc, rows = self.doctor()
        self.assertEqual(rc, 0, rows)
        (note,) = self.rows(rows, "note", harness="opencode", type="mcp")
        self.assertIn("edits its flags in place", note["detail"])


class StateTest(DoctorCase):
    def test_missing_parked_item_is_an_error(self) -> None:
        self.assertEqual(self.run_cli("disable", "skill", "demo-skill")[0], 0)
        self.assertEqual(self.doctor()[0], 0)
        shutil.rmtree(self.home / "skills-disabled" / "demo-skill")
        rc, rows = self.doctor()
        self.assertEqual(rc, 1)
        (bad,) = self.rows(rows, "error", harness="claude", type="skill", name="demo-skill")
        self.assertIn("parked item missing", bad["detail"])
        self.assertIn("agent-toggle", bad["detail"])

    def test_status_reports_missing_parked_item_read_only(self) -> None:
        self.run_cli("disable", "skill", "demo-skill")
        self.run_cli("disable", "skill", "demo-skill", "--harness", "codex")
        rc, env = self.run_json("status")
        self.assertEqual(self.rows(env["results"], "stale"), [])
        shutil.rmtree(self.tmp / ".codex" / "skills-disabled" / "demo-skill")
        before = snapshot(self.tmp)
        rc, env = self.run_json("status")
        self.assertEqual(rc, 0)                      # a health report, not a failure
        self.assertEqual(snapshot(self.tmp), before)
        (row,) = self.rows(env["results"], "stale")
        self.assertEqual((row["harness"], row["type"], row["name"]), ("codex", "skill", "demo-skill"))
        self.assertIn("fix:", row["detail"])
        self.assertIn("agent-toggle enable skill demo-skill --harness codex", row["detail"])
        self.assertTrue(any("parked item missing" in w for w in env["warnings"]))
        rc, env = self.run_json("status", "--harness", "claude")    # filtered out
        self.assertEqual(self.rows(env["results"], "stale"), [])
        rc, rows = self.doctor()                     # doctor shares the same check and text
        (bad,) = self.rows(rows, "error", name="demo-skill")
        self.assertEqual(bad["detail"], row["detail"])

    def test_missing_origin_dir_is_an_error(self) -> None:
        self.run_cli("disable", "skill", "demo-skill")
        shutil.rmtree(self.home / "skills")
        rc, rows = self.doctor()
        self.assertEqual(rc, 1)
        self.assertTrue(any("origin" in r["detail"] for r in self.rows(rows, "error")))

    def test_tampered_entry_is_refused(self) -> None:
        self.run_cli("disable", "skill", "demo-skill")
        state = json.loads(fs.state_file().read_text(encoding="utf-8"))
        state["disabled"]["claude:skill:demo-skill"]["parked_at"] = str(self.tmp / "elsewhere")
        fs.state_file().write_text(json.dumps(state), encoding="utf-8")
        rc, rows = self.doctor()
        self.assertEqual(rc, 1)
        (bad,) = self.rows(rows, "error", name="demo-skill")
        self.assertIn("refused", bad["detail"])

    def test_malformed_entries_are_rows_not_crashes(self) -> None:
        store.save_state({"version": 3, "disabled": {
            "claude:skill:a-skill": {}, "claude:skill:b-skill": {"harness": "nope"},
            "claude:skill:c-skill": {"mechanism": ["move"], "harness": "claude"},
            "claude:skill:d-skill": {"mechanism": "move", "harness": "claude", "parked_at": ["x"]},
            "claude:mcp:e-mcp": {"mechanism": "remove_backup", "harness": "claude", "backup": 7},
            "not-a-key": {}}})
        fs.parked_dir().mkdir(parents=True)
        (fs.parked_dir() / "deadbeef").write_text("x", encoding="utf-8")   # a file, not a dir
        (fs.parked_dir() / ".DS_Store").write_text("x", encoding="utf-8")
        rc, rows = self.doctor()
        self.assertEqual(rc, 1)
        self.assertEqual(len(self.rows(rows, "error")), 6, rows)
        self.assertEqual([r for r in rows if "DS_Store" in r["detail"]], [])

    def test_missing_backup_is_an_error(self) -> None:
        fs.backup_dir().mkdir(parents=True)
        bp = fs.backup_dir() / "claude__example-mcp.json"
        entry = {"mechanism": "remove_backup", "harness": "claude", "type": "mcp",
                 "name": "example-mcp", "backend": "claude-json", "backup": str(bp),
                 "scope": "user", "project": None, "at": "2026-01-01T00:00:00+0000"}
        store.save_state({"version": 3, "disabled": {"claude:mcp:example-mcp": entry}})
        rc, rows = self.doctor()
        self.assertEqual(rc, 1)
        (bad,) = self.rows(rows, "error", type="mcp", name="example-mcp")
        self.assertIn("backup missing", bad["detail"])
        bp.write_text("{}", encoding="utf-8")
        self.assertEqual(self.doctor()[0], 0)

    def test_flag_entry_drift(self) -> None:
        self.assertEqual(self.run_cli("disable", "mcp", "example-mcp", "--harness", "opencode")[0], 0)
        self.assertEqual(self.doctor()[0], 0)
        text = self.oc.read_text(encoding="utf-8")
        self.oc.write_text(text.replace("false", "true"), encoding="utf-8")   # re-enabled by hand
        rc, rows = self.doctor()
        self.assertEqual(rc, 1)
        (bad,) = self.rows(rows, "error", harness="opencode", name="example-mcp")
        self.assertIn("outside this tool", bad["detail"])
        self.oc.write_text('{"mcp": {}}', encoding="utf-8")                   # entry gone
        rc, rows = self.doctor()
        self.assertEqual(rc, 1)
        self.assertTrue(self.rows(rows, "error", harness="opencode", name="example-mcp"))

    def test_project_dir_gone(self) -> None:
        proj = self.tmp / "work" / "app"
        (proj / ".claude" / "skills" / "demo-skill").mkdir(parents=True)
        (proj / ".claude" / "skills" / "demo-skill" / "SKILL.md").write_text("x", encoding="utf-8")
        self.assertEqual(self.run_cli("disable", "skill", "demo-skill", "--project", str(proj))[0], 0)
        self.assertEqual(self.doctor()[0], 0)
        shutil.rmtree(proj)
        rc, rows = self.doctor()
        self.assertEqual(rc, 1)
        bad = self.rows(rows, "error", name="demo-skill")
        self.assertEqual(len(bad), 1, rows)
        self.assertIn("project dir gone", bad[0]["detail"])
        self.assertTrue((fs.parked_dir() / store.project_digest(proj)).exists())  # never deleted

    def test_empty_park_dir_left_by_a_project_enable_is_silent(self) -> None:
        proj = self.tmp / "work" / "app"
        (proj / ".claude" / "skills" / "demo-skill").mkdir(parents=True)
        (proj / ".claude" / "skills" / "demo-skill" / "SKILL.md").write_text("x", encoding="utf-8")
        for verb in ("disable", "enable"):
            self.assertEqual(self.run_cli(verb, "skill", "demo-skill", "--project", str(proj))[0], 0)
        park = fs.parked_dir() / store.project_digest(proj)
        self.assertFalse(park.exists())                  # enable prunes it now (G12)
        (park / "skills-disabled").mkdir(parents=True)   # a leftover from an older version
        rc, rows = self.doctor()
        self.assertEqual(rc, 0, rows)
        self.assertEqual(self.rows(rows, "warn"), [])

    @unittest.skipUnless(CAN_SYMLINK, "cannot create symlinks here")
    def test_stray_file_and_dangling_link_in_parked_are_still_warned(self) -> None:
        fs.private_dir(fs.state_dir())
        fs.private_dir(fs.parked_dir())
        (fs.parked_dir() / "stray.txt").write_text("x", encoding="utf-8")
        (fs.parked_dir() / "dangling").symlink_to(self.tmp / "nowhere")
        rows = self.rows(self.doctor()[1], "warn")
        self.assertEqual({r["name"] for r in rows}, {"stray.txt", "dangling"})

    def test_non_object_entry_does_not_hide_the_others(self) -> None:
        store.save_state({"version": 3, "disabled": {
            "claude:skill:a-skill": 5,
            "claude:mcp:e-mcp": {"mechanism": "remove_backup", "harness": "claude",
                                 "backup": str(fs.backup_dir() / "gone.json")}}})
        rc, rows = self.doctor()
        self.assertEqual(rc, 1)
        self.assertEqual({r["name"] for r in self.rows(rows, "error") if r["name"]},
                         {"a-skill", "e-mcp"}, rows)

    def test_orphans_are_reported_not_deleted(self) -> None:
        orphan = fs.parked_dir() / "deadbeef" / "skills-disabled" / "old-skill"
        orphan.mkdir(parents=True)
        (orphan / "SKILL.md").write_text("x", encoding="utf-8")
        stray = self.home / "skills-disabled" / "stray-skill"
        stray.mkdir(parents=True)
        fs.backup_dir().mkdir(parents=True)
        (fs.backup_dir() / "claude__gone-mcp.json").write_text("{}", encoding="utf-8")
        rc, rows = self.doctor()
        self.assertEqual(rc, 0, rows)
        text = " ".join(r["detail"] for r in self.rows(rows, "warn") + self.rows(rows, "note"))
        for needle in ("deadbeef", "stray-skill", "claude__gone-mcp.json"):
            self.assertIn(needle, text)
        self.assertTrue(orphan.exists() and stray.exists())

    def test_orphan_fix_depends_on_the_live_copy(self) -> None:
        for n, text in (("same", "a"), ("drift", "old")):
            self.write(f"skills-disabled/{n}/SKILL.md", text)
            self.write(f"skills/{n}/SKILL.md", "a" if n == "same" else "new")
        self.write("skills-disabled/solo/SKILL.md")
        self.write("agents-disabled/solo-agent.md")
        rc, rows = self.doctor("--harness", "claude")
        self.assertEqual(rc, 0, rows)
        got = {r["name"]: r for r in self.rows(rows, "warn") if r.get("orphan")}
        self.assertEqual({n: r["orphan"] for n, r in got.items()},
                         {"same": "identical", "drift": "differs", "solo": "parked-only",
                          "solo-agent.md": "parked-only"})
        self.assertIn("delete the parked copy", got["same"]["detail"])
        self.assertIn("compare", got["drift"]["detail"])
        # parked-only: put it back, then re-park through the tool so state records it
        self.assertIn("`agent-toggle disable skill solo`", got["solo"]["detail"])
        self.assertIn("`agent-toggle disable agent solo-agent`", got["solo-agent.md"]["detail"])
        self.assertTrue((self.home / "skills-disabled/same").exists())    # still read-only


class ReadOnlyTest(DoctorCase):
    def test_doctor_writes_nothing(self) -> None:
        self.run_cli("disable", "skill", "demo-skill")
        self.run_cli("disable", "mcp", "example-mcp", "--harness", "opencode")
        shutil.rmtree(self.home / "skills-disabled" / "demo-skill")      # an error row
        (self.tmp / ".codex" / "config.toml").write_text("[x", encoding="utf-8")
        before = snapshot(self.tmp)
        for argv in ((), ("--json",)):
            self.assertEqual(self.run_cli("doctor", *argv)[0], 1)
        self.assertEqual(snapshot(self.tmp), before)
        self.assertEqual(self.cli_calls, [])
        self.assertFalse(fs.lock_file().exists())


class FixTest(DoctorCase):
    """offer_fixes: each fix doctor can run itself is asked y/n; yes runs it, no changes nothing."""

    def fixes(self, answer: bool = True) -> tuple[list[str], Result]:
        out, asked = Result("doctor", json_mode=True), []
        fixes = doctor.cmd_doctor(None, out)
        doctor.offer_fixes(fixes, out, lambda q: asked.append(q) or answer)
        return asked, out

    def test_no_answers_change_nothing(self) -> None:
        fs.private_dir(fs.state_dir())
        fs.state_dir().chmod(0o755)
        self.write("skills-disabled/solo/SKILL.md")
        before = snapshot(self.tmp)
        asked, _ = self.fixes(answer=False)
        self.assertEqual(len(asked), 1 if os.name == "nt" else 2, asked)   # no loose-mode fix on Windows
        self.assertEqual(snapshot(self.tmp), before)

    @unittest.skipIf(os.name == "nt", "no POSIX modes")
    def test_loose_modes_are_chmodded(self) -> None:
        self.run_cli("disable", "skill", "demo-skill")
        fs.state_dir().chmod(0o755)
        fs.state_file().chmod(0o644)
        asked, out = self.fixes()
        self.assertEqual(sorted(q.split()[1] for q in asked), ["600", "700"])
        self.assertFalse(fs.too_open(fs.state_dir()) or fs.too_open(fs.state_file(), 0o077))
        self.assertEqual(out.exit_code(), 0)
        self.assertEqual([r for r in out.rows if r["status"] == "warn"], [])

    def test_stale_flag_entry_is_enabled(self) -> None:
        self.run_cli("disable", "mcp", "example-mcp", "--harness", "opencode")
        self.oc.write_text(self.oc.read_text(encoding="utf-8").replace("false", "true"),
                           encoding="utf-8")
        asked, out = self.fixes()
        self.assertIn("agent-toggle enable mcp example-mcp --harness opencode", asked[0])
        self.assertEqual(out.exit_code(), 0)
        self.assertEqual(self.state(), {})
        self.assertEqual(self.doctor()[0], 0)

    def test_missing_origin_dir_is_recreated_and_restored(self) -> None:
        self.run_cli("disable", "skill", "demo-skill")
        shutil.rmtree(self.home / "skills")
        self.fixes()
        self.assertTrue((self.home / "skills" / "demo-skill").is_dir())
        self.assertEqual(self.state(), {})

    def test_orphans_identical_deleted_parked_only_restored_differs_left(self) -> None:
        for n, text in (("same", "a"), ("drift", "old")):
            self.write(f"skills-disabled/{n}/SKILL.md", text)
            self.write(f"skills/{n}/SKILL.md", "a" if n == "same" else "new")
        self.write("skills-disabled/solo/SKILL.md", "solo")
        asked, _ = self.fixes()
        self.assertEqual(len(asked), 2, asked)               # differs needs a human
        self.assertFalse((self.home / "skills-disabled/same").exists())
        self.assertEqual((self.home / "skills/solo/SKILL.md").read_text(encoding="utf-8"), "solo")
        self.assertTrue((self.home / "skills-disabled/drift").exists())

    def test_backup_deleted_only_when_its_server_is_live_again(self) -> None:
        self.claude_json({"mcpServers": {"back-mcp": {"command": "x"}}})
        fs.private_dir(fs.backup_dir())
        live, gone = (fs.backup_dir() / f"claude__{n}.json" for n in ("back-mcp", "gone-mcp"))
        for b in (live, gone):
            b.write_text("{}", encoding="utf-8")
        asked, out = self.fixes()
        self.assertEqual(len([q for q in asked if q.startswith("Delete")]), 1, asked)
        self.assertEqual(out.exit_code(), 0)        # its chmod 600 fix, asked after, is a no-op
        self.assertFalse(live.exists())
        self.assertTrue(gone.exists())

    def test_interrupted_op_is_settled(self) -> None:
        self.run_cli("disable", "skill", "demo-skill")
        raw = json.loads(fs.state_file().read_text(encoding="utf-8"))
        entry = raw["disabled"].pop("claude:skill:demo-skill")
        raw["pending"] = {"claude:skill:demo-skill": {"action": "disable", "entry": entry}}
        fs.state_file().write_text(json.dumps(raw), encoding="utf-8")
        asked, out = self.fixes()
        self.assertEqual(len(asked), 1, asked)
        raw = json.loads(fs.state_file().read_text(encoding="utf-8"))
        self.assertEqual((raw.get("pending") or {}, list(raw["disabled"])),
                         ({}, ["claude:skill:demo-skill"]))
        self.assertEqual(out.exit_code(), 0)


class CompanionTest(DoctorCase):
    """Companion files: parked file present, no orphans, never both live and parked."""

    def setUp(self) -> None:
        super().setUp()
        self.write("scripts/only-mine.sh", "#!/bin/sh")
        self.write("skills/solo/SKILL.md", "run scripts/only-mine.sh")
        self.assertEqual(self.run_cli("disable", "skill", "solo")[0], 0)
        (comp,) = self.state()["claude:skill:solo"]["companions"]
        self.origin, self.parked = Path(comp["from"]), Path(comp["to"])
        self.assertTrue(self.parked.is_file())

    def comp_rows(self, rows: list[dict], status: str) -> list[dict]:
        return [r for r in self.rows(rows, status) if "companion" in r["detail"]]

    def test_healthy_companion_is_silent(self) -> None:
        rc, rows = self.doctor()
        self.assertEqual((rc, self.comp_rows(rows, "error"), self.comp_rows(rows, "warn")), (0, [], []))

    def test_missing_parked_companion_is_an_error_with_a_fix(self) -> None:
        self.parked.unlink()
        rc, rows = self.doctor()
        self.assertEqual(rc, 1)
        (bad,) = self.comp_rows(rows, "error")
        self.assertEqual((bad["type"], bad["name"]), ("skill", "solo"))
        self.assertIn("parked file missing", bad["detail"])
        self.assertIn("fix: ", bad["detail"])
        self.assertIn("agent-toggle enable skill solo", bad["detail"])

    def test_live_and_parked_together_is_an_error(self) -> None:
        self.origin.write_text("copy", encoding="utf-8")
        rc, rows = self.doctor()
        self.assertEqual(rc, 1)
        (bad,) = self.comp_rows(rows, "error")
        self.assertIn("both live", bad["detail"])
        self.assertIn("fix: ", bad["detail"])

    def test_orphan_parked_companion_is_a_warning(self) -> None:
        stray = self.parked.parent / "stray.sh"
        stray.write_text("x", encoding="utf-8")
        rc, rows = self.doctor()
        self.assertEqual(rc, 0)
        (w,) = self.comp_rows(rows, "warn")
        self.assertIn(str(stray), w["detail"])
        self.assertIn("fix: ", w["detail"])

    def test_pending_entries_are_not_reported_as_companion_problems(self) -> None:
        raw = json.loads(fs.state_file().read_text(encoding="utf-8"))
        entry = raw["disabled"].pop("claude:skill:solo")
        raw["pending"] = {"claude:skill:solo": {"action": "disable", "entry": entry}}
        fs.state_file().write_text(json.dumps(raw), encoding="utf-8")
        _, rows = self.doctor()
        self.assertEqual(self.comp_rows(rows, "warn") + self.comp_rows(rows, "error"), [])

    def test_killed_enable_keeps_the_key_in_both_and_is_not_a_companion_error(self) -> None:
        raw = json.loads(fs.state_file().read_text(encoding="utf-8"))
        raw["pending"] = {"claude:skill:solo": {"action": "enable",
                                                "entry": raw["disabled"]["claude:skill:solo"]}}
        fs.state_file().write_text(json.dumps(raw), encoding="utf-8")
        self.parked.rename(self.origin)               # the enable already moved it back
        _, rows = self.doctor()
        self.assertEqual(self.comp_rows(rows, "warn") + self.comp_rows(rows, "error"), [])


if __name__ == "__main__":
    unittest.main()
