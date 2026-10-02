"""doctor: read-only drift + state health check (DESIGN s6 "Harness drift")."""
from __future__ import annotations

import json
import shutil
import unittest

from test_cli_surface import CliCase, snapshot
from test_conformance import FIXTURES

from agent_toggle import fs, store

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

    def test_missing_mcp_key_is_layout_changed(self) -> None:
        (self.tmp / ".claude.json").write_text('{"theme": "dark"}', encoding="utf-8")
        rc, rows = self.doctor()
        self.assertEqual(rc, 1)
        (bad,) = self.rows(rows, "error", harness="claude", type="mcp")
        self.assertIn("layout changed", bad["detail"])
        self.assertIn("mcpServers", bad["detail"])

    def test_broken_openclaw_json_is_layout_changed(self) -> None:
        self.claw.write_text('{"skills": {"entries": ', encoding="utf-8")
        rc, rows = self.doctor()
        self.assertEqual(rc, 1)
        bad = self.rows(rows, "error", harness="openclaw")
        self.assertTrue(bad and all("layout changed" in r["detail"] for r in bad), rows)

    def test_missing_flag_prefix_is_layout_changed(self) -> None:
        self.oc.write_text('{"servers": {}}', encoding="utf-8")
        rc, rows = self.doctor()
        self.assertEqual(rc, 1)
        (bad,) = self.rows(rows, "error", harness="opencode", type="mcp")
        self.assertIn("layout changed", bad["detail"])

    def test_one_row_per_broken_shared_flag_file(self) -> None:
        self.claw.write_text('{"skills": ', encoding="utf-8")
        rows = self.rows(self.doctor("--harness", "openclaw")[1], "error")
        self.assertEqual(len(rows), 1, rows)

    def test_jsonc_claude_json_is_an_error(self) -> None:
        (self.tmp / ".claude.json").write_text('{"mcpServers": {},}', encoding="utf-8")
        self.assertEqual(self.doctor("--harness", "claude")[0], 1)

    def test_jsonc_is_an_unsupported_note_not_corruption(self) -> None:
        self.oc.write_text('{\n  // comment\n  "mcp": {"example-mcp": {"enabled": true,},},\n}\n',
                           encoding="utf-8")
        rc, rows = self.doctor()
        self.assertEqual(rc, 0, rows)
        (note,) = self.rows(rows, "note", harness="opencode", type="mcp")
        self.assertIn("unsupported format", note["detail"])


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

    def test_orphans_are_reported_not_deleted(self) -> None:
        orphan = fs.parked_dir() / "deadbeef" / "skills-disabled" / "old-skill"
        orphan.mkdir(parents=True)
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


if __name__ == "__main__":
    unittest.main()
