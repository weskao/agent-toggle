"""Platform behaviour (DESIGN s5.11, s6.1 row 5): ids, case, replace retry, nested names."""
from __future__ import annotations

import contextlib
import io
import os
import unittest
from unittest import mock

from base import SandboxCase

from agent_toggle import fs
from agent_toggle import mechanisms as mech
from agent_toggle.backends import plugin_cli


class PluginIdTest(SandboxCase):
    def toggle(self, name: str, dry_run: bool = False) -> tuple[int, list[dict]]:
        out = mech.Result()
        state = {"version": 3, "disabled": {}}
        with contextlib.redirect_stdout(io.StringIO()):
            fails = mech.toggle_plugin("disable", [name], state, "claude", out, dry_run)
        return fails, out.rows

    def test_shell_metacharacters_are_refused_before_any_subprocess(self) -> None:
        self.cli_rc = 0
        for bad in ("evil&calc", "a|b", "a^b", "50%PATH%", "a b", 'a"b', "a;b", "a<b", "a>b",
                    "a`b", "$(x)", "a\\b", "", "café@m"):
            for dry in (False, True):
                fails, rows = self.toggle(bad, dry)
                self.assertEqual(fails, 1, bad)
                self.assertIn("refused", rows[0]["detail"], bad)
        self.assertEqual(self.cli_calls, [])

    def test_real_plugin_ids_pass(self) -> None:
        self.cli_rc = 0
        for good in ("superpowers@marketplace", "my-plugin_2.0", "scope:name@a.b/c"):
            self.assertEqual(self.toggle(good)[0], 0, good)
        self.assertEqual(len(self.cli_calls), 3)


class ReplaceRetryTest(SandboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.src, self.dst = self.tmp / "a", self.tmp / "b"
        self.src.write_text("x", encoding="utf-8")
        p = mock.patch.object(fs.time, "sleep")
        p.start()
        self.addCleanup(p.stop)

    def test_windows_retries_then_succeeds(self) -> None:
        real, calls = os.replace, []

        def flaky(a, b):
            calls.append(1)
            if len(calls) < 3:
                raise PermissionError("in use")
            real(a, b)

        with mock.patch.object(fs, "WIN", True), mock.patch.object(fs.os, "replace", flaky):
            fs.replace(self.src, self.dst)
        self.assertEqual((len(calls), self.dst.read_text(encoding="utf-8")), (3, "x"))

    def test_windows_gives_up_loudly_after_three_tries(self) -> None:
        calls = []

        def locked(a, b):
            calls.append(1)
            raise PermissionError("in use")

        with mock.patch.object(fs, "WIN", True), mock.patch.object(fs.os, "replace", locked):
            with self.assertRaisesRegex(PermissionError, "in use by another program"):
                fs.replace(self.src, self.dst)
        self.assertEqual(len(calls), 3)

    def test_other_platforms_do_not_retry(self) -> None:
        calls = []

        def locked(a, b):
            calls.append(1)
            raise PermissionError("denied")

        with mock.patch.object(fs, "WIN", False), mock.patch.object(fs.os, "replace", locked):
            with self.assertRaises(PermissionError):
                fs.replace(self.src, self.dst)
        self.assertEqual(len(calls), 1)

    def test_atomic_write_goes_through_the_retrying_replace(self) -> None:
        fs.atomic_write(self.dst, "hello")
        self.assertEqual(self.dst.read_text(encoding="utf-8"), "hello")
        self.assertEqual([p.name for p in self.tmp.glob(".b.*.tmp")], [])


class CaseRulesTest(SandboxCase):
    def test_resolve_item_follows_the_filesystem_case_rules(self) -> None:
        """Insensitive FS (macOS, Windows) finds `Demo` for `demo`; sensitive (Linux) does not."""
        self.write("skills/Demo/SKILL.md")
        insensitive = (self.home / "skills" / "demo").exists()
        found = mech.resolve_item(self.home / "skills", "demo")
        self.assertEqual(found is not None, insensitive)

    def test_nested_names_use_a_colon_on_every_os(self) -> None:
        self.write("commands/group/sub/tool.md")
        self.assertEqual(mech.live_names(self.home / "commands", "command"), ["group:sub:tool"])


class ClaudeBinTest(SandboxCase):
    def test_off_path_windows_install_is_found_by_its_exe_name(self) -> None:
        """The native Windows installer writes ~/.local/bin/claude.exe; PATH may lack it."""
        plugin_cli.which = lambda name: None
        exe = self.tmp / ".local" / "bin" / "claude.exe"
        exe.parent.mkdir(parents=True)
        exe.touch()
        exe.chmod(0o755)
        self.assertEqual(plugin_cli.claude_bin(), str(exe))


if __name__ == "__main__":
    unittest.main()
