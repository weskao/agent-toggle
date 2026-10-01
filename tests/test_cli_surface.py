"""CLI surface: --json envelope, exit codes, --dry-run, --version, old spellings."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path

from base import SandboxCase

from agent_toggle import __version__, cli, fs

ENVELOPE_KEYS = {"ok", "command", "results", "warnings", "needs_new_session"}
ROW_KEYS = {"harness", "type", "name", "action", "status", "detail"}


def snapshot(root: Path) -> dict[str, str]:
    """Every path under root -> content hash / link target / 'dir' (+ mode)."""
    snap: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for n in dirnames + filenames:
            p = Path(dirpath) / n
            rel = str(p.relative_to(root))
            mode = oct(p.lstat().st_mode)
            if p.is_symlink():
                snap[rel] = f"link:{os.readlink(p)}:{mode}"
            elif p.is_dir():
                snap[rel] = f"dir:{mode}"
            else:
                snap[rel] = f"{hashlib.sha256(p.read_bytes()).hexdigest()}:{mode}"
    return snap


class CliCase(SandboxCase):
    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def run_json(self, *argv: str) -> tuple[int, dict]:
        rc, out, _ = self.run_cli(*argv, "--json")
        return rc, json.loads(out)         # the WHOLE stdout must be one document

    def claude_json(self, cfg: dict) -> None:
        (self.tmp / ".claude.json").write_text(json.dumps(cfg))


class EnvelopeTest(CliCase):
    def check_envelope(self, env: dict, command: str) -> None:
        self.assertEqual(set(env), ENVELOPE_KEYS)
        self.assertEqual(env["command"], command)
        for row in env["results"]:
            self.assertEqual(set(row) & ROW_KEYS, ROW_KEYS)

    def test_status_and_list_envelopes(self) -> None:
        rc, env = self.run_json("status")
        self.assertEqual(rc, 0)
        self.check_envelope(env, "status")
        self.assertTrue(env["ok"])
        self.assertFalse(env["needs_new_session"])
        claude = [r for r in env["results"] if r["harness"] == "claude"][0]
        self.assertEqual(claude["status"], "installed")
        rc, env = self.run_json("list")
        self.assertEqual((rc, env["results"], env["ok"]), (0, [], True))
        self.check_envelope(env, "list")

    def test_disable_then_enable_envelopes(self) -> None:
        self.write("skills/demo-skill/SKILL.md")
        rc, env = self.run_json("disable", "skill", "demo-skill")
        self.assertEqual(rc, 0)
        self.check_envelope(env, "disable")
        self.assertTrue(env["ok"] and env["needs_new_session"])
        row = env["results"][0]
        self.assertEqual((row["harness"], row["type"], row["name"], row["action"], row["status"]),
                         ("claude", "skill", "demo-skill", "disable", "ok"))

        rc, env = self.run_json("list", "skill")
        self.assertEqual([r["name"] for r in env["results"]], ["demo-skill"])

        rc, env = self.run_json("enable", "skill", "demo-skill")
        self.assertEqual(rc, 0)
        self.check_envelope(env, "enable")
        self.assertTrue(env["needs_new_session"])
        self.assertTrue((self.home / "skills/demo-skill/SKILL.md").is_file())

    def test_json_mode_keeps_text_out_of_stdout(self) -> None:
        self.write("skills/demo-skill/SKILL.md")
        rc, out, err = self.run_cli("disable", "skill", "demo-skill", "--json")
        self.assertEqual(rc, 0)
        self.assertEqual(err, "")
        self.assertEqual(json.loads(out)["command"], "disable")   # nothing but the document

    def test_warnings_travel_in_the_envelope(self) -> None:
        self.write("skills/demo-skill/SKILL.md")      # park dir is not gitignored here
        _, env = self.run_json("disable", "skill", "demo-skill")
        self.assertTrue(any("NOT gitignored" in w for w in env["warnings"]))

    def test_error_envelope_when_not_ok(self) -> None:
        rc, env = self.run_json("disable", "skill", "missing")
        self.assertEqual(rc, 1)
        self.assertFalse(env["ok"])
        self.assertEqual(env["results"][0]["status"], "error")
        self.assertFalse(env["needs_new_session"])


class ExitCodeTest(CliCase):
    def test_0_ok(self) -> None:
        self.assertEqual(self.run_cli("status")[0], 0)

    def test_1_partial_failure(self) -> None:
        self.write("skills/real/SKILL.md")
        rc, env = self.run_json("disable", "skill", "real", "ghost")
        self.assertEqual(rc, 1)
        self.assertEqual([r["status"] for r in env["results"]], ["ok", "error"])
        self.assertTrue(env["needs_new_session"])      # one item really moved

    def test_2_usage_error(self) -> None:
        self.assertEqual(self.run_cli("disable", "widget", "x")[0], 2)
        self.assertEqual(self.run_cli("disable", "skill")[0], 2)
        self.assertEqual(self.run_cli("frobnicate")[0], 2)
        self.assertEqual(self.run_cli()[0], 2)
        self.assertEqual(self.run_cli("list", "--dry-run")[0], 2)    # disable/enable only
        rc, env = self.run_json("disable", "widget", "x")
        self.assertEqual(rc, 2)
        self.assertFalse(env["ok"])
        self.assertEqual(set(env), ENVELOPE_KEYS)

    def test_3_locked(self) -> None:
        self.write("skills/demo-skill/SKILL.md")
        with fs.lock():
            rc, env = self.run_json("disable", "skill", "demo-skill")
        self.assertEqual(rc, 3)
        self.assertFalse(env["ok"])
        self.assertTrue((self.home / "skills/demo-skill").is_dir())

    def test_4_unsupported_pair_and_missing_harness(self) -> None:
        (self.tmp / ".grok").mkdir()
        self.assertEqual(self.run_cli("disable", "mcp", "x", "--harness", "grok")[0], 4)
        self.assertEqual(self.run_cli("disable", "skill", "x", "--harness", "codex")[0], 4)
        rc, env = self.run_json("disable", "skill", "x", "--harness", "codex")
        self.assertEqual((rc, env["ok"]), (4, False))

    def test_4_plugin_on_harness_without_plugin_cli(self) -> None:
        (self.tmp / ".codex").mkdir()
        rc, env = self.run_json("disable", "plugin", "p", "--harness", "codex")
        self.assertEqual(rc, 4)
        self.assertEqual(env["results"][0]["status"], "unsupported")


class DryRunTest(CliCase):
    def assert_untouched(self, *argv: str) -> dict:
        before = snapshot(self.tmp)
        rc, env = self.run_json(*argv, "--dry-run")
        self.assertEqual(snapshot(self.tmp), before)       # byte-identical, no new paths
        self.assertEqual(rc, 0, env)
        self.assertFalse(env["needs_new_session"])
        return env

    def test_skill_disable_dry_run_writes_nothing(self) -> None:
        self.write("skills/demo-skill/SKILL.md", "uses scripts/only-mine.sh and scripts/shared.sh")
        self.assertFalse((self.tmp / ".agent-toggle").exists())
        self.write("scripts/only-mine.sh", "echo mine")
        self.write("scripts/shared.sh", "echo shared")
        self.write("commands/other.md", "calls scripts/shared.sh")
        env = self.assert_untouched("disable", "skill", "demo-skill")
        row = env["results"][0]
        self.assertEqual((row["action"], row["status"]), ("would-disable", "planned"))
        self.assertEqual([Path(c["from"]).name for c in row["companions"]], ["only-mine.sh"])
        self.assertTrue(any("shared.sh" in w for w in env["warnings"]))

    def test_dry_run_plan_matches_real_run(self) -> None:
        self.write("skills/demo-skill/SKILL.md", "uses scripts/only-mine.sh")
        self.write("scripts/only-mine.sh", "echo mine")
        planned = self.assert_untouched("disable", "skill", "demo-skill")["results"][0]
        _, env = self.run_json("disable", "skill", "demo-skill")
        real = env["results"][0]
        self.assertEqual(planned["parked_at"], real["parked_at"])
        self.assertEqual(planned["companions"], real["companions"])

    def test_skill_enable_dry_run_writes_nothing(self) -> None:
        self.write("skills/demo-skill/SKILL.md")
        self.assertEqual(self.run_json("disable", "skill", "demo-skill")[0], 0)
        env = self.assert_untouched("enable", "skill", "demo-skill")
        self.assertEqual(env["results"][0]["action"], "would-enable")

    def test_dry_run_reports_a_plan_that_would_fail(self) -> None:
        before = snapshot(self.tmp)
        rc, env = self.run_json("disable", "skill", "ghost", "--dry-run")
        self.assertEqual((rc, env["results"][0]["status"]), (1, "error"))
        self.assertEqual(snapshot(self.tmp), before)

    def test_mcp_claude_disable_dry_run_writes_nothing(self) -> None:
        self.claude_json({"mcpServers": {"example-mcp": {"command": "x"}}})
        self.cli_rc = 0
        env = self.assert_untouched("disable", "mcp", "example-mcp")
        self.assertEqual(self.cli_calls, [])                # CLI never invoked
        self.assertEqual(env["results"][0]["action"], "would-disable")

    def test_mcp_claude_enable_dry_run_writes_nothing(self) -> None:
        self.claude_json({"mcpServers": {"example-mcp": {"command": "x"}}})
        self.cli_rc = 0
        self.assertEqual(self.run_json("disable", "mcp", "example-mcp")[0], 0)
        calls = len(self.cli_calls)
        self.assert_untouched("enable", "mcp", "example-mcp")
        self.assertEqual(len(self.cli_calls), calls)

    def test_mcp_codex_disable_dry_run_writes_nothing(self) -> None:
        codex = self.tmp / ".codex"
        codex.mkdir()
        (codex / "config.toml").write_text('[mcp_servers.example-mcp]\ncommand = "x"\n')
        env = self.assert_untouched("disable", "mcp", "example-mcp", "--harness", "codex")
        self.assertEqual(env["results"][0]["status"], "planned")

    def test_plugin_dry_run_does_not_call_the_cli(self) -> None:
        self.assert_untouched("disable", "plugin", "example-plugin")
        self.assertEqual(self.cli_calls, [])

    def test_dry_run_does_not_wait_on_the_lock(self) -> None:
        self.write("skills/demo-skill/SKILL.md")
        with fs.lock():
            self.assertEqual(self.run_cli("disable", "skill", "demo-skill", "--dry-run")[0], 0)


class SpellingTest(CliCase):
    def test_version(self) -> None:
        for argv in (["--version"], ["status", "--version"]):
            rc, out, _ = self.run_cli(*argv)
            self.assertEqual(rc, 0)
            self.assertIn(__version__, out)

    def test_multiple_names_and_harness_position(self) -> None:
        (self.tmp / ".codex" / "skills").mkdir(parents=True)
        for n in ("a", "b"):
            (self.tmp / ".codex" / "skills" / n).mkdir()
        # --harness before the subcommand, after it, and between positionals all work
        self.assertEqual(self.run_cli("--harness", "codex", "disable", "skill", "a")[0], 0)
        self.assertEqual(self.run_cli("disable", "skill", "b", "--harness", "codex")[0], 0)
        self.assertFalse(list((self.tmp / ".codex" / "skills").iterdir()))
        self.assertEqual(self.run_cli("enable", "--harness", "codex", "skill", "a", "b")[0], 0)
        self.assertEqual(len(list((self.tmp / ".codex" / "skills").iterdir())), 2)

    def test_list_filters_and_pick_alias(self) -> None:
        self.write("skills/demo-skill/SKILL.md")
        self.run_cli("disable", "skill", "demo-skill")
        rc, out, _ = self.run_cli("list", "skill")
        self.assertIn("demo-skill", out)
        self.assertIn("1 disabled", out)
        self.assertIn("nothing disabled", self.run_cli("list", "agent")[1])
        self.assertIn("nothing disabled", self.run_cli("list", "--harness", "codex")[1])

    def test_human_output_keeps_its_text(self) -> None:
        self.write("skills/demo-skill/SKILL.md")
        rc, out, _ = self.run_cli("disable", "skill", "demo-skill")
        self.assertEqual(rc, 0)
        self.assertIn("v skill demo-skill disabled", out)
        rc, out, err = self.run_cli("disable", "skill", "demo-skill", "--dry-run")
        self.assertIn("x skill demo-skill: not found", out)
        self.assertEqual(rc, 1)
