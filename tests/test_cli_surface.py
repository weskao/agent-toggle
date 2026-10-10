"""CLI surface: --json envelope, exit codes, --dry-run, --version, old spellings."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

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
        (self.tmp / ".claude.json").write_text(json.dumps(cfg), encoding="utf-8")


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
        self.git_init(self.tmp)                       # a tracked $HOME holds the park dir
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
        (self.tmp / ".openclaw").mkdir()
        self.assertEqual(self.run_cli("disable", "mcp", "x", "--harness", "openclaw")[0], 4)
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
        (codex / "config.toml").write_text('[mcp_servers.example-mcp]\ncommand = "x"\n', encoding="utf-8")
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

    def test_every_command_accepts_a_leading_double_dash(self) -> None:
        self.write("skills/demo-skill/SKILL.md")
        self.run_cli("profile", "save", "work")
        proj = self.tmp / "proj"
        (proj / ".claude" / "skills" / "demo-skill").mkdir(parents=True)
        cases = (
            ["status"], ["list"], ["list", "skill"], ["ui"], ["ui", "--dry-run"],
            ["cost"], ["cost", "--type", "skill"], ["cost", "--harness", "codex"],
            ["cost", "--project", str(proj)], ["ui", "--dry-run", "--project", str(proj)],
            ["disable", "skill", "demo-skill", "--dry-run"],
            ["enable", "skill", "demo-skill", "--dry-run"],
            ["install-shims", "--dry-run"],
            ["profile", "list"], ["profile", "diff", "work"],
            ["profile", "apply", "work", "--dry-run"],
            ["undo", "--dry-run"], ["enable", "--all", "--dry-run"],
            ["doctor"], ["doctor", "--harness", "claude"], ["update"],
            ["disable", "skill", "demo-skill", "--project", str(proj), "--dry-run"],
        )
        for argv in cases:
            plain = self.run_cli(*argv, "--json")
            dashed = self.run_cli(f"--{argv[0]}", *argv[1:], "--json")
            self.assertEqual(plain, dashed, argv)
        # global flags before the command do not hide it
        self.assertEqual(self.run_cli("--json", "--status")[:2], self.run_cli("--json", "status")[:2])
        self.assertEqual(self.run_cli("--harness=codex", "--list"),
                         self.run_cli("--harness=codex", "list"))
        self.assertEqual(self.run_cli("--pick", "--json"), self.run_cli("pick", "--json"))
        # delete / rename change disk, so each spelling gets its own profile
        for a, b in (("one", "uno"), ("two", "dos")):
            self.run_cli("profile", "save", a)
        renamed = [self.run_json(*argv) for argv in (("profile", "rename", "one", "uno"),
                                                     ("--profile", "rename", "two", "dos"))]
        deleted = [self.run_json(*argv) for argv in (("profile", "delete", "uno"),
                                                     ("--profile", "delete", "dos"))]
        for (rc1, e1), (rc2, e2) in (renamed, deleted):
            self.assertEqual((rc1, e1["command"], [r["action"] for r in e1["results"]]),
                             (rc2, e2["command"], [r["action"] for r in e2["results"]]))
            self.assertEqual(rc1, 0)
        self.assertEqual(self.run_cli("profile", "delete", "uno"),
                         self.run_cli("--profile", "delete", "uno"))   # same exit-2 error

    def test_help_topics_behave_the_same_with_and_without_dashes(self) -> None:
        for topic in ("version", "help", "status", "config"):
            self.assertEqual(self.run_cli("help", f"--{topic}"), self.run_cli("help", topic), topic)
        for bad in ("bogus", "--bogus", "xxconfig"):   # not normalised to config
            rc, out, err = self.run_cli("help", bad)
            self.assertEqual((rc, out), (2, ""), bad)
            self.assertIn("no such command", err)
        # global flags after help are not topics
        want = self.run_cli("help", "status")
        for flags in (["--json"], ["-v"], ["--color", "never"], ["--color=never"],
                      ["--harness", "codex"], ["--harness=codex"]):
            self.assertEqual(self.run_cli("help", *flags, "status"), want, flags)
        self.assertEqual(self.run_cli("help", "--json")[:2], self.run_cli("help")[:2])
        # a literal `--` ends the options; it is not the topic
        self.assertEqual(self.run_cli("help", "--", "config"), self.run_cli("help", "config"))

    def test_config_accepts_a_leading_double_dash(self) -> None:
        with mock.patch.dict(sys.modules, {"telegram_kit": None}):     # never the real keychain
            for argv in (["config"], ["config", "test"], ["config", "sync-ci", "--dry-run"]):
                plain = self.run_cli(*argv, "--json")
                self.assertEqual(plain, self.run_cli(f"--{argv[0]}", *argv[1:], "--json"), argv)
                if argv != ["config"]:          # bare config is the settings menu: no kit needed
                    self.assertEqual(plain[0], 4)

    def test_color_flag_before_a_double_dash_command(self) -> None:
        self.write("skills/demo-skill/SKILL.md")
        for flag in (["--color", "never"], ["--color=never"], ["--color", "always", "--json"]):
            for cmd in ("status", "list", "cost", "doctor"):
                self.assertEqual(self.run_cli(*flag, f"--{cmd}"), self.run_cli(*flag, cmd), (flag, cmd))
        self.assertEqual(self.run_cli("--color", "never", "--harness", "codex", "--list"),
                         self.run_cli("--harness", "codex", "list"))
        self.assertEqual(self.run_cli("--color", "bogus", "status")[0], 2)

    def test_help_and_version_without_dashes(self) -> None:
        for word in ("help", "version"):
            self.assertEqual(self.run_cli(word), self.run_cli(f"--{word}"))
        self.assertEqual(self.run_cli("help"), self.run_cli("-h"))
        self.assertIn(__version__, self.run_cli("version")[1])
        self.assertIn("USAGE", self.run_cli("help")[1])

    def test_version_not_behind_latest_changelog_entry(self) -> None:
        # v0.3.0-v0.5.0 shipped with __version__ still at 0.2.0; ahead is fine (bumped, unreleased)
        changelog = Path(__file__).resolve().parents[1] / "CHANGELOG.md"
        top = next(line for line in changelog.read_text(encoding="utf-8").splitlines()
                   if line.startswith("## [") and "Unreleased" not in line)
        released = top[4:top.index("]")]
        as_tuple = lambda v: tuple(int(p) for p in v.split("."))  # noqa: E731
        self.assertGreaterEqual(as_tuple(__version__), as_tuple(released), top)

    def test_per_command_help_in_both_spellings(self) -> None:
        # help X == --help X == X --help, and each with --X for X: one output, exit 0
        for cmd in cli.COMMANDS:
            want = self.run_cli("help", cmd)
            self.assertEqual(want[0], 0, cmd)
            for argv in (["--help", cmd], [cmd, "--help"], [cmd, "-h"], ["help", f"--{cmd}"],
                         ["--help", f"--{cmd}"], [f"--{cmd}", "--help"]):
                self.assertEqual(self.run_cli(*argv), want, argv)
        self.assertEqual(self.run_cli("--json", "help", "status"), self.run_cli("--json", "status",
                                                                               "--help"))

    def test_bare_words_after_the_command_stay_names(self) -> None:
        self.write("skills/help/SKILL.md")
        self.assertEqual(self.run_cli("disable", "skill", "help")[0], 0)
        self.assertEqual(self.run_cli("enable", "skill", "help")[0], 0)

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


class UnexpectedErrorTest(CliCase):
    """Any failure still yields exactly one JSON document and rc 1, never a traceback."""

    def assert_one_error_doc(self, *argv: str) -> dict:
        rc, out, err = self.run_cli(*argv, "--json")
        self.assertEqual(rc, 1, (out, err))
        env = json.loads(out)                        # the WHOLE stdout is one document
        self.assertFalse(env["ok"])
        self.assertEqual(env["results"][-1]["status"], "error")
        self.assertNotIn("Traceback", out + err)
        return env

    def bad_state(self, data: bytes) -> None:
        fs.state_dir().mkdir(parents=True, exist_ok=True)
        fs.state_file().write_bytes(data)

    def test_state_top_level_not_an_object(self) -> None:
        self.bad_state(b"[]")
        self.assert_one_error_doc("list")

    def test_state_invalid_utf8(self) -> None:
        self.bad_state(b"\xff\xfe{")
        self.assert_one_error_doc("list")

    def test_state_version_not_an_int(self) -> None:
        self.bad_state(b'{"version": "3", "disabled": {}}')
        env = self.assert_one_error_doc("status")
        self.assertIn("version", env["results"][-1]["detail"])

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                     "needs POSIX modes and a non-root user")
    def test_unreadable_codex_config(self) -> None:
        codex = self.tmp / ".codex"
        codex.mkdir()
        cfg = codex / "config.toml"
        cfg.write_text('[mcp_servers.example-mcp]\ncommand = "x"\n', encoding="utf-8")
        cfg.chmod(0)
        self.addCleanup(cfg.chmod, 0o600)
        env = self.assert_one_error_doc("disable", "mcp", "example-mcp", "--harness", "codex")
        self.assertIn("PermissionError", env["results"][-1]["detail"])

    def test_text_mode_is_one_line_unless_verbose(self) -> None:
        self.bad_state(b"[]")
        rc, out, err = self.run_cli("list")
        self.assertEqual((rc, out), (1, ""))
        self.assertEqual(len(err.strip().splitlines()), 1)
        with mock.patch.object(cli, "cmd_list", side_effect=RuntimeError("boom")):
            self.bad_state(b'{"version": 3, "disabled": {}}')
            rc, _, err = self.run_cli("list")
            self.assertEqual((rc, err.strip()), (1, "error: RuntimeError: boom"))
            rc, _, err = self.run_cli("list", "-v")
            self.assertIn("Traceback", err)

    def test_ui_apply_failure_still_saves_state(self) -> None:
        try:
            from agent_toggle.ui import picker
        except ImportError:
            self.skipTest("curses unavailable")

        from agent_toggle import ops

        def half_done(plan, state, out, dry_run, batch, headers):
            state["disabled"]["claude:skill:demo-skill"] = {"type": "skill", "name": "demo-skill"}
            raise RuntimeError("second item crashed")

        row = picker.Row("claude", "skill", "demo-skill", True)
        row.staged = False
        with mock.patch.object(picker, "pick", return_value=[row]), \
                mock.patch.object(ops, "_dispatch", half_done):
            rc, _, err = self.run_cli("ui")
        self.assertEqual(rc, 1)
        self.assertIn("claude:skill:demo-skill", json.loads(fs.state_file().read_text(encoding="utf-8"))["disabled"])


class RuleWarningTest(CliCase):
    def test_disabling_a_rule_warns_about_safety_constraints(self) -> None:
        self.write("rules/demo-rule.md")
        self.write("skills/demo-skill/SKILL.md")
        for dry in (("--dry-run",), ()):
            rc, env = self.run_json("disable", "rule", "demo-rule", *dry)
            self.assertEqual(rc, 0)
            self.assertEqual(sum("safety constraints" in w for w in env["warnings"]), 1)
        rc, _, err = self.run_cli("enable", "rule", "demo-rule")
        self.assertNotIn("safety constraints", err)
        rc, _, err = self.run_cli("disable", "rule", "demo-rule")
        self.assertIn("safety constraints", err)              # humans see it on stderr
        rc, env = self.run_json("disable", "skill", "demo-skill")
        self.assertFalse(any("safety constraints" in w for w in env["warnings"]))


class DryRunNoShellOutTest(CliCase):
    def test_unknown_mcp_dry_run_never_calls_claude_mcp_get(self) -> None:
        self.claude_json({"mcpServers": {}})
        self.cli_rc = 0
        rc, env = self.run_json("disable", "mcp", "ghost-mcp", "--dry-run")
        self.assertEqual((rc, env["results"][0]["status"]), (1, "error"))
        self.assertEqual(self.cli_calls, [])
        self.run_json("disable", "mcp", "ghost-mcp")           # a real run still asks
        self.assertEqual(self.cli_calls[0][0][:2], ["mcp", "get"])
