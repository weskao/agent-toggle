"""install-shims: writes the skill shim into installed harnesses only, idempotently."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import unittest
import unittest.mock
from pathlib import Path

from base import CAN_SYMLINK, SandboxCase

from agent_toggle import cli
from agent_toggle.harnesses import harnesses

REPO = Path(cli.__file__).resolve().parent.parent


def shim(home: Path) -> Path:
    return home / "skills" / "agent-toggle" / "SKILL.md"


class InstallShimsCase(SandboxCase):
    def setUp(self) -> None:
        super().setUp()
        (self.tmp / ".codex").mkdir()          # ~/.claude comes from the sandbox

    def run_cli(self, *argv: str) -> tuple[int, dict]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.main(["install-shims", "--json", *argv])
        return rc, json.loads(buf.getvalue())

    def installed(self) -> set[str]:
        return {h for h, rec in harnesses().items() if shim(rec.home).is_file()}

    def test_writes_only_existing_harnesses(self) -> None:
        rc, env = self.run_cli()
        self.assertEqual(rc, 0)
        self.assertEqual(self.installed(), {"claude", "codex"})
        self.assertEqual({r["harness"] for r in env["results"] if r["status"] == "ok"},
                         {"claude", "codex"})
        self.assertEqual({r["harness"] for r in env["results"] if r["status"] == "skipped"},
                         {"grok", "opencode", "openclaw", "copilot", "vibe", "devin", "agy"})
        text = shim(self.tmp / ".claude").read_text(encoding="utf-8")
        self.assertIn("--json", text)
        self.assertNotIn("__AGENT_TOGGLE_ROOT__", text)
        self.assertIn("agent_toggle.py", text)     # checkout fallback line kept

    def test_rerun_is_idempotent(self) -> None:
        self.run_cli()
        first = shim(self.tmp / ".claude").read_text(encoding="utf-8")
        rc, _ = self.run_cli()
        self.assertEqual(rc, 0)
        self.assertEqual(shim(self.tmp / ".claude").read_text(encoding="utf-8"), first)

    def test_dry_run_writes_nothing(self) -> None:
        (self.tmp / ".claude" / ".gitignore").write_text("junk\n", encoding="utf-8")
        rc, env = self.run_cli("--dry-run")
        self.assertEqual(rc, 0)
        self.assertEqual(self.installed(), set())
        self.assertEqual((self.tmp / ".claude" / ".gitignore").read_text(encoding="utf-8"), "junk\n")
        self.assertTrue(all(r["status"] in ("planned", "skipped") for r in env["results"]))

    def opencode_home(self, cfg: dict | None) -> Path:
        oc = self.tmp / ".config" / "opencode"
        oc.mkdir(parents=True)
        if cfg is not None:
            (oc / "opencode.json").write_text(json.dumps(cfg), encoding="utf-8")
        return oc

    def test_opencode_shim_follows_skills_paths(self) -> None:
        oc = self.opencode_home({"skills": {"paths": ["$HOME/mine/skills", "~/other"]}})
        (self.tmp / "mine").mkdir()
        rc, env = self.run_cli("--harness", "opencode")
        self.assertEqual(rc, 0)
        self.assertTrue(shim(self.tmp / "mine").is_file())         # first path, created
        self.assertFalse(shim(oc).exists())                        # not the default dir
        self.assertFalse((self.tmp / "other").exists())

    def test_opencode_shim_defaults_without_redirect_or_with_unusable_one(self) -> None:
        oc = self.opencode_home(None)
        self.assertEqual(self.run_cli("--harness", "opencode")[0], 0)
        self.assertTrue(shim(oc).is_file())
        shutil.rmtree(oc / "skills")
        (oc / "opencode.json").write_text(
            json.dumps({"skills": ["/no/such/parent/skills"]}), encoding="utf-8")
        self.assertEqual(self.run_cli("--harness", "opencode")[0], 0)
        self.assertTrue(shim(oc).is_file())

    def test_opencode_redirect_keeps_the_marker_refusal(self) -> None:
        self.opencode_home({"skills": {"paths": ["~/mine/skills"]}})
        mine = shim(self.tmp / "mine")
        mine.parent.mkdir(parents=True)
        mine.write_text("my own skill\n", encoding="utf-8")
        rc, env = self.run_cli("--harness", "opencode")
        self.assertEqual(rc, 1)
        self.assertIn("lacks the agent-toggle shim marker", env["results"][0]["detail"])
        self.assertEqual(mine.read_text(encoding="utf-8"), "my own skill\n")

    def test_targeted_opencode_run_never_overwrites_the_owners_shim(self) -> None:
        self.opencode_home({"skills": {"paths": ["~/.codex/skills"]}})
        self.assertEqual(self.run_cli("--harness", "codex")[0], 0)
        before = shim(self.tmp / ".codex").read_text(encoding="utf-8")
        rc, env = self.run_cli("--harness", "opencode")
        self.assertEqual(env["results"][0]["status"], "skipped")
        self.assertIn("belongs to codex", env["results"][0]["detail"])
        self.assertEqual(shim(self.tmp / ".codex").read_text(encoding="utf-8"), before)

    def test_shim_not_written_twice_into_a_shared_dir(self) -> None:
        self.opencode_home({"skills": ["~/.claude/skills"]})       # opencode -> claude's dir
        rc, env = self.run_cli()
        self.assertEqual(rc, 0)
        self.assertEqual(shim(self.tmp / ".claude").read_text(encoding="utf-8"),
                         cli.shim_text("claude"))                   # not overwritten by opencode's
        (row,) = [r for r in env["results"] if r["harness"] == "opencode"]
        self.assertEqual(row["status"], "skipped")

    def test_opencode_shim_skipped_when_an_alias_dir_already_serves_the_same_one(self) -> None:
        oc = self.opencode_home(None)
        twin = self.tmp / ".agents" / "skills" / "agent-toggle" / "SKILL.md"
        twin.parent.mkdir(parents=True)
        twin.write_text(cli.shim_text("opencode"), encoding="utf-8")
        rc, env = self.run_cli("--harness", "opencode")
        self.assertEqual(rc, 0)
        (row,) = env["results"]
        self.assertEqual(row["status"], "skipped")
        self.assertEqual(row["detail"], f"covered by {twin}")
        self.assertFalse(shim(oc).exists())

    def test_opencode_shim_still_written_beside_the_claude_one_and_noted(self) -> None:
        # the claude shim tells the agent `--harness` defaults to claude: wrong for OpenCode
        self.opencode_home(None)
        rc, env = self.run_cli()
        self.assertEqual(rc, 0)
        (row,) = [r for r in env["results"] if r["harness"] == "opencode"]
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["also_seen"], [str(shim(self.tmp / ".claude"))])
        self.assertEqual(shim(self.tmp / ".config" / "opencode").read_text(encoding="utf-8"),
                         cli.shim_text("opencode"))

    def test_no_harness_is_exit_4(self) -> None:
        for h in (".claude", ".codex"):
            os.rename(self.tmp / h, self.tmp / (h + "-gone"))
        rc, env = self.run_cli()
        self.assertEqual(rc, 4)
        self.assertFalse(env["ok"])

    def test_park_dirs_appended_once_from_table(self) -> None:
        ignore = self.tmp / ".claude" / ".gitignore"
        ignore.write_text("skills-disabled/\nkeep", encoding="utf-8")          # one present, no trailing newline
        self.run_cli()
        self.run_cli()
        lines = ignore.read_text(encoding="utf-8").splitlines()
        for d in ("skills-disabled/", "agents-disabled/", "commands-disabled/"):
            self.assertEqual(lines.count(d), 1, d)
        self.assertIn("keep", lines)
        self.assertFalse((self.tmp / ".codex" / ".gitignore").exists())   # never created

    def test_foreign_file_is_refused_others_proceed(self) -> None:
        mine = shim(self.tmp / ".claude")
        mine.parent.mkdir(parents=True)
        mine.write_text("my own skill\n", encoding="utf-8")
        for dry in ((), ("--dry-run",)):            # the dry run reports the same plan
            rc, env = self.run_cli(*dry)
            self.assertEqual(rc, 1)
            self.assertFalse(env["ok"])
            (bad,) = [r for r in env["results"] if r["status"] == "error"]
            self.assertEqual(bad["harness"], "claude")
            self.assertIn("lacks the agent-toggle shim marker", bad["detail"])
            self.assertIn("fix:", bad["detail"])
            ok = {r["harness"] for r in env["results"] if r["status"] in ("ok", "planned")}
            self.assertEqual(ok, {"codex"})
        self.assertEqual(mine.read_text(encoding="utf-8"), "my own skill\n")
        self.assertTrue(shim(self.tmp / ".codex").is_file())

    def test_every_harness_refused_is_exit_1_not_4(self) -> None:
        for h in (".claude", ".codex"):
            shim(self.tmp / h).parent.mkdir(parents=True)
            shim(self.tmp / h).write_text("x", encoding="utf-8")
        self.assertEqual(self.run_cli()[0], 1)

    def test_marked_file_is_updated_in_place(self) -> None:
        dest = shim(self.tmp / ".claude")
        dest.parent.mkdir(parents=True)
        dest.write_text(f"old text\n{cli.SHIM_MARKER}\n", encoding="utf-8")
        rc, _ = self.run_cli("--harness", "claude")
        self.assertEqual(rc, 0)
        self.assertEqual(dest.read_text(encoding="utf-8"), cli.shim_text("claude"))

    def test_pre_marker_shim_is_recognised_and_upgraded(self) -> None:
        dest = shim(self.tmp / ".claude")
        dest.parent.mkdir(parents=True)
        dest.write_text("---\nname: agent-toggle\ndescription: old\n---\n\n# agent-toggle\n\nold\n",
                        encoding="utf-8")
        self.assertEqual(self.run_cli("--harness", "claude")[0], 0)
        self.assertIn(cli.SHIM_MARKER, dest.read_text(encoding="utf-8"))
        # a user's own skill that merely borrows the name is still refused
        dest.write_text("---\nname: agent-toggle\n---\nmine\n", encoding="utf-8")
        self.assertEqual(self.run_cli("--harness", "claude")[0], 1)

    @unittest.skipUnless(CAN_SYMLINK, "needs symlink privilege")
    def test_symlinked_shim_is_refused(self) -> None:
        target = self.tmp / "elsewhere.md"
        target.write_text(cli.SHIM_MARKER, encoding="utf-8")
        shim(self.tmp / ".claude").parent.mkdir(parents=True)
        shim(self.tmp / ".claude").symlink_to(target)
        rc, env = self.run_cli("--harness", "claude")
        self.assertEqual(rc, 1)
        self.assertIn("symlink", env["results"][0]["detail"])
        self.assertEqual(target.read_text(encoding="utf-8"), cli.SHIM_MARKER)

    def test_shim_is_picked_per_harness(self) -> None:
        self.run_cli()
        claude = shim(self.tmp / ".claude").read_text(encoding="utf-8")
        codex = shim(self.tmp / ".codex").read_text(encoding="utf-8")
        self.assertIn("--project", claude)                  # claude.md.tmpl
        self.assertIn("--harness codex --json", codex)       # generic.md.tmpl, harness filled in
        self.assertNotIn("__HARNESS__", codex)
        for text in (claude, codex):
            self.assertIn(cli.SHIM_MARKER, text)
            self.assertTrue(text.startswith("---\nname: agent-toggle\n"))   # frontmatter first

    def test_every_template_carries_marker_and_safety_text(self) -> None:
        tmpls = sorted((Path(cli.__file__).parent / "shims").glob("*.md.tmpl"))
        self.assertIn("generic.md.tmpl", [t.name for t in tmpls])
        for t in tmpls:
            text = t.read_text(encoding="utf-8")
            self.assertIn(cli.SHIM_MARKER, text, t.name)
            self.assertIn("explicit request", text, t.name)
            self.assertIn("never disable", text.lower(), t.name)
            self.assertIn("--dry-run", text, t.name)
            self.assertIn("safety constraints", text, t.name)

    def test_template_in_package_dir(self) -> None:
        self.assertTrue((Path(cli.__file__).parent / "shims" / "claude.md.tmpl").is_file())
        self.assertFalse((REPO / "shims").exists())

    @unittest.skipIf(os.name == "nt", "no POSIX modes")
    def test_offers_doctor_fixes_on_a_terminal_only(self) -> None:
        from agent_toggle import fs
        fs.private_dir(fs.state_dir())
        fs.state_dir().chmod(0o755)
        self.run_cli()                                   # --json: never asks
        self.assertTrue(fs.too_open(fs.state_dir()))
        asked, buf = [], io.StringIO()
        with unittest.mock.patch.object(cli, "_interactive", return_value=True), \
                unittest.mock.patch("builtins.input", lambda q: asked.append(q) or "y"), \
                contextlib.redirect_stdout(buf):
            self.assertEqual(cli.main(["install-shims"]), 0)
        self.assertEqual(len(asked), 1, asked)
        self.assertIn("chmod 700", asked[0])
        self.assertIn("group/world readable", buf.getvalue())     # the problem, shown first
        self.assertFalse(fs.too_open(fs.state_dir()))

    @unittest.skipIf(os.name == "nt", "install.sh is a POSIX wrapper; Windows uses the console script")
    def test_install_sh_wrapper(self) -> None:
        env = {**os.environ, "HOME": str(self.tmp), "USERPROFILE": str(self.tmp)}
        p = subprocess.run(["bash", str(REPO / "install.sh")], env=env,
                           capture_output=True, text=True, timeout=60, encoding="utf-8")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.installed(), {"claude", "codex"})


if __name__ == "__main__":
    unittest.main()
