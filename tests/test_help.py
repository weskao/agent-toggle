"""Styled help: grouped overview, one per-command page for every spelling, colour rules."""
from __future__ import annotations

import argparse
import contextlib
import io
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

from base import SandboxCase

from agent_toggle import cli, help, i18n, output, settings

ESC = "\x1b["
REPO = Path(__file__).resolve().parents[1]


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


class HelpTest(SandboxCase):
    def run_cli(self, *argv: str, out=None) -> tuple[int, str, str]:
        out, err = out or io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_overview_groups_commands_and_lists_global_options_once(self) -> None:
        rc, out, _ = self.run_cli("help")
        self.assertEqual(rc, 0)
        heads = ["TOGGLE", "INSPECT", "SETUP", "GLOBAL OPTIONS", "EXAMPLES", "EXIT CODES"]
        at = [out.index(f"\n{h}\n") for h in heads]
        self.assertEqual(at, sorted(at))
        sections = dict(zip(heads, (out[a:b] for a, b in zip(at, at[1:] + [len(out)]))))
        for head, names in (("TOGGLE", ("ui, pick", "disable", "enable", "undo", "profile")),
                            ("INSPECT", ("status", "list", "cost", "doctor")),
                            ("SETUP", ("config", "install-shims", "migrate"))):
            for n in names:
                self.assertIn(f"\n  {n} ", sections[head], (head, n))
        self.assertEqual(sum(ln.lstrip().startswith("--json") for ln in out.splitlines()), 1)
        title = next(ln for ln in out.splitlines() if ln.startswith("agent-toggle "))   # under the logo
        self.assertIn(cli.__version__, title)
        self.assertIn("every command also works with a leading --", out)

    def test_per_command_help_is_the_same_in_every_spelling(self) -> None:
        for name in help.commands():
            want = self.run_cli("help", name)
            self.assertEqual(want[0], 0)
            self.assertIn(f"agent-toggle {name}", want[1].splitlines()[0])
            for argv in (["--help", name], [name, "--help"], [name, "-h"]):
                self.assertEqual(self.run_cli(*argv), want, argv)
        self.assertEqual(self.run_cli("help", "pick"), self.run_cli("help", "ui"))
        # a global flag's value after help is not the topic
        self.assertEqual(self.run_cli("help", "--color", "never"), self.run_cli("help"))
        self.assertEqual(self.run_cli("--help", "--harness", "codex", "cost"),
                         self.run_cli("help", "cost"))

    def test_per_command_help_has_no_global_flag_block(self) -> None:
        for name in help.commands():
            out = self.run_cli("help", name)[1]
            self.assertNotIn("GLOBAL OPTIONS", out)
            self.assertNotIn("--verbose", out)
            self.assertEqual(sum("agent-toggle help`" in ln for ln in out.splitlines()), 1)

    def test_every_parser_option_and_choice_is_documented(self) -> None:
        parser = cli.build_parser()
        common = {f for a in parser._actions for f in a.option_strings}
        sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
        for name in help.commands():
            text = help.command_help(name, False)
            for a in sub.choices[name]._actions:
                for f in set(a.option_strings) - common:
                    self.assertIn(f, text, (name, f))
                for c in (a.choices or ()) if not a.option_strings else ():
                    self.assertIn(c, text, (name, c))
        self.assertEqual(set(help.commands()) | {"pick"}, set(sub.choices))

    def test_color_follows_tty_flag_and_no_color(self) -> None:
        with mock.patch.object(output, "_vt_enable", return_value=True):
            self.assertIn(ESC, self.run_cli("help", out=_Tty())[1])
            self.assertNotIn(ESC, self.run_cli("help")[1])                   # not a TTY
            self.assertNotIn(ESC, self.run_cli("help", "--color", "never", out=_Tty())[1])
            self.assertIn(ESC, self.run_cli("--color", "always", "help", "cost")[1])
            with mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
                self.assertNotIn(ESC, self.run_cli("help", out=_Tty())[1])
                self.assertNotIn(ESC, self.run_cli("cost", "--help", out=_Tty())[1])

    def test_an_ascii_stdout_gets_help_not_a_crash(self) -> None:
        env = {**os.environ, "LANG": "C", "LC_ALL": "C", "PYTHONIOENCODING": "ascii"}
        for argv in (["help"], ["help", "config"]):
            done = subprocess.run([sys.executable, "-m", "agent_toggle", *argv],
                                  capture_output=True, encoding="ascii", errors="replace",
                                  env=env, cwd=str(REPO), check=False)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertNotIn("Traceback", done.stderr)
            self.assertIn("agent-toggle", done.stdout)
            self.assertNotIn("?", done.stdout.splitlines()[0], argv)   # an ASCII dash, not '?'

    def test_help_help_version_and_a_topic_after_global_flags(self) -> None:
        self.assertEqual(self.run_cli("help", "help"), self.run_cli("help"))
        rc, out, _ = self.run_cli("help", "version")
        self.assertEqual(rc, 0)
        self.assertIn("agent-toggle version | --version", out)
        self.assertEqual(self.run_cli("help", "--harness", "codex", "--config"),
                         self.run_cli("help", "config"))

    def test_double_dash_before_a_topic_or_after_a_command(self) -> None:
        self.assertEqual(self.run_cli("help", "--", "--status"), self.run_cli("help", "status"))
        for argv in (("list", "--", "--help"), ("status", "--", "-h")):
            self.assertEqual(self.run_cli(*argv)[0], 2, argv)

    def test_no_command_prints_help_off_a_tty_and_opens_the_picker_on_one(self) -> None:
        rc, out, _ = self.run_cli()
        self.assertEqual((rc, out), (2, self.run_cli("help")[1]))     # 2: still a usage error
        self.assertEqual(self.run_cli("--color", "never")[1], self.run_cli("help")[1])
        with mock.patch.object(sys, "stdin", _Tty()), mock.patch.object(cli, "cmd_ui") as ui:
            rc, _, _ = self.run_cli(out=_Tty())
        self.assertEqual(rc, 0)
        ui.assert_called_once()

    def test_unknown_topic_is_a_usage_error(self) -> None:
        rc, out, err = self.run_cli("help", "nope")
        self.assertEqual((rc, out), (2, ""))
        self.assertIn("nope", err)

    def test_default_harness_and_language_settings(self) -> None:
        self.addCleanup(i18n.set_language, "en")
        settings.set("default_harness", "codex")
        self.assertIn("target harness (default: codex)", self.run_cli("help")[1])
        self.assertIn("this acts on codex", self.run_cli("disable", "--help")[1])
        settings.set("language", "zh-TW")
        out = self.run_cli("help")[1]
        self.assertIn("用法", out)
        self.assertNotIn("USAGE", out)
