"""Colour: ANSI only when asked for or on a TTY; plain output is byte-identical."""
from __future__ import annotations

import contextlib
import io
import os
import unittest
from unittest import mock

from base import SandboxCase

from agent_toggle import cli, output

ESC = "\x1b["
_REAL_VT = output._vt_enable      # captured before any patching


class _BrokenTty(io.StringIO):
    def isatty(self) -> bool:
        raise ValueError("closed")


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


class ColorTest(SandboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("skills/demo-skill/SKILL.md", "# demo")
        # fake _Tty has no console handle; the real VT step is Windows-only (see _REAL_VT)
        vt = mock.patch.object(output, "_vt_enable", return_value=True)
        vt.start()
        self.addCleanup(vt.stop)

    def run_cli(self, *argv: str, out=None, err=None) -> tuple[int, str, str]:
        out, err = out or io.StringIO(), err or io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_plain_when_not_a_tty_or_opted_out(self) -> None:
        cmds = (["status"], ["list"], ["cost"], ["doctor"], ["disable", "skill", "nope"])
        plain = {c[0]: self.run_cli(*c) for c in cmds}
        for c in plain.values():
            self.assertNotIn(ESC, c[1] + c[2])
        self.assertEqual(plain["cost"], self.run_cli("--color", "never", "cost"))
        for name, val in (("NO_COLOR", "1"), ("TERM", "dumb")):
            with mock.patch.dict(os.environ, {name: val}):
                # a TTY stays plain under NO_COLOR / TERM=dumb
                tty = self.run_cli("cost", out=_Tty(), err=_Tty())
                self.assertNotIn(ESC, tty[1])
                self.assertEqual(tty[1], plain["cost"][1])
        for mode in ("always", "never"):
            with mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
                self.assertEqual(ESC in self.run_cli("--color", mode, "cost")[1], mode == "always")

    def test_always_colors_text_and_equals_plain_once_stripped(self) -> None:
        import re
        for cmd in ("status", "cost", "doctor"):
            plain = self.run_cli(cmd)
            colored = self.run_cli("--color", "always", cmd)
            self.assertIn(ESC, colored[1], cmd)
            self.assertEqual(re.sub(r"\x1b\[[0-9;]*m", "", colored[1]), plain[1], cmd)
        self.assertEqual(self.run_cli("--color=always", "cost")[1],
                         self.run_cli("--color", "always", "cost")[1])

    def test_json_is_never_colored(self) -> None:
        for cmd in ("status", "cost", "doctor", "list"):
            rc, out, err = self.run_cli("--color", "always", cmd, "--json")
            self.assertNotIn(ESC, out + err, cmd)
        with mock.patch.dict(os.environ, {"FORCE_COLOR": "1"}):
            self.assertNotIn(ESC, self.run_cli("cost", "--json")[1])

    def test_errors_and_warnings_follow_stderr_not_stdout(self) -> None:
        # stdout is a TTY, stderr a pipe: only stdout may carry escapes
        rc, out, err = self.run_cli("disable", "skill", "nope", out=_Tty())
        self.assertEqual(rc, 1)
        self.assertIn(ESC, out)
        rc, out, err = self.run_cli("bogus", err=io.StringIO())
        self.assertEqual(rc, 2)
        self.assertNotIn(ESC, err)
        self.assertIn(ESC, self.run_cli("--color", "always", "bogus")[2])      # argparse error
        self.assertIn(ESC, self.run_cli("bogus", err=_Tty())[2])

    def test_force_color_and_state_does_not_leak_between_runs(self) -> None:
        with mock.patch.dict(os.environ, {"FORCE_COLOR": "1"}):
            self.assertIn(ESC, self.run_cli("cost")[1])
        self.assertNotIn(ESC, self.run_cli("cost")[1])
        self.assertIn(ESC, self.run_cli("--color", "always", "cost")[1])
        self.assertNotIn(ESC, self.run_cli("cost")[1])

    def test_precedence_of_no_color_force_color_term_and_never(self) -> None:
        tty = _Tty()
        for env, expect in (({"NO_COLOR": "1", "FORCE_COLOR": "1"}, False),   # only NO_COLOR beats FORCE_COLOR
                            ({"TERM": "dumb", "FORCE_COLOR": "1"}, True)):    # FORCE_COLOR beats TERM=dumb
            with mock.patch.dict(os.environ, env):
                self.assertIs(output.use_color(tty), expect, env)
                self.assertEqual(ESC in self.run_cli("cost", out=io.StringIO())[1], expect, env)
        with mock.patch.dict(os.environ, {"FORCE_COLOR": "1"}):
            self.assertNotIn(ESC, self.run_cli("--color", "never", "cost", out=_Tty())[1])
            self.assertFalse(output.use_color(tty, "never"))
        for val in ("0", ""):                          # FORCE_COLOR=0 / empty does not force
            with mock.patch.dict(os.environ, {"FORCE_COLOR": val, "TERM": "dumb"}):
                self.assertIs(output.use_color(_Tty()), False, val)       # does not beat TERM=dumb
                self.assertIs(output.use_color(io.StringIO()), False, val)  # nor a pipe
        self.assertIs(output.use_color(_BrokenTty()), False)   # isatty() raising == not a tty

    def test_warning_label_follows_stderr_tty_not_stdout(self) -> None:
        argv = ("disable", "skill", "demo-skill")
        self.git_init()                                # the "NOT gitignored" warning needs a repo
        _, out, err = self.run_cli(*argv, out=io.StringIO(), err=_Tty())
        self.assertIn("\x1b[33mWARNING\x1b[0m", err)
        self.assertNotIn(ESC, out)
        self.setUp()                                   # fresh sandbox: demo-skill is enabled again
        self.git_init()
        _, out, err = self.run_cli(*argv, out=_Tty(), err=io.StringIO())
        self.assertIn("WARNING", err)
        self.assertNotIn(ESC, err)

    def test_list_and_ok_mark_use_their_colors_and_every_segment_resets(self) -> None:
        import re
        rc, out, _ = self.run_cli("--color", "always", "disable", "skill", "demo-skill")
        self.assertIn("\x1b[32mv\x1b[0m skill demo-skill", out)             # ok mark: green
        listed = self.run_cli("--color", "always", "list")[1]
        self.run_cli("disable", "skill", "demo-skill")
        self.assertRegex(self.run_cli("--color", "always", "cost")[1],
                         r"\x1b\[2m[^\x1b]*parked, would save[^\x1b]*\x1b\[0m")
        self.assertIn("\x1b[36mclaude   \x1b[0m", self.run_cli("--color", "always", "cost")[1])
        self.assertIn("\x1b[36mclaude   \x1b[0m", self.run_cli("--color", "always", "status")[1])
        self.assertIn("\x1b[31m  x skill nope",
                      self.run_cli("--color", "always", "disable", "skill", "nope")[1])
        self.assertIn("\x1b[36mclaude   \x1b[0m", listed)                   # harness name: cyan
        for text in (out, listed, self.run_cli("--color", "always", "status")[1]):
            self.assertEqual(len(re.findall(r"\x1b\[(?!0m)\d+m", text)), text.count("\x1b[0m"))
            self.assertEqual(len(re.findall(r"\x1b\[(?!0m)\d+m[^\x1b]+\x1b\[0m", text)),
                             text.count("\x1b[0m"))

    def test_windows_console_without_ansi_stays_plain(self) -> None:
        for mode in ("auto", "always"):
            for vt, expect in ((False, False), (True, True)):
                with mock.patch.dict(os.environ, {"FORCE_COLOR": "1"} if mode == "auto" else {}):
                    with mock.patch.object(output, "_vt_enable", return_value=vt) as vte, \
                            mock.patch("os.name", "nt"):
                        self.assertIs(output.use_color(_Tty(), mode), expect, (mode, vt))
                        vte.assert_called_once()
        with mock.patch.object(output, "_vt_enable", return_value=False) as vte, \
                mock.patch("os.name", "nt"):
            self.assertTrue(output.use_color(io.StringIO(), "always"))   # not a tty: no VT step
            vte.assert_not_called()
        with mock.patch.object(output, "_vt_enable", return_value=False) as vte, \
                mock.patch("os.name", "nt"):
            self.assertFalse(output.use_color(_Tty()))                   # auto-on-tty, VT fails
            self.assertFalse(output.use_color(io.StringIO()))
            self.assertEqual(vte.call_count, 1)

    @unittest.skipIf(os.name == "nt", "the real _vt_enable needs a Windows console")
    def test_vt_enable_is_false_off_windows(self) -> None:
        self.assertIs(_REAL_VT(_Tty()), False)


if __name__ == "__main__":
    unittest.main()
