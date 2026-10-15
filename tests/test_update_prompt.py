"""`update` / `--update` delegate to update_kit; the background hint is one stderr line."""
from __future__ import annotations

import io
import subprocess
import sys
import unittest
from unittest import mock

import update_kit

from agent_toggle import __version__, i18n, settings, update_prompt


class AfterStepsTest(unittest.TestCase):
    def steps(self, claude, plugins):
        with mock.patch.object(update_prompt.plugin_cli, "claude_bin", return_value=claude), \
                mock.patch.object(update_prompt.plugin_cli, "list_plugins",
                                  return_value=[{"id": i} for i in plugins]):
            return update_prompt.after_steps()

    def test_the_plugin_is_updated_only_when_it_is_installed(self) -> None:
        shims = ["agent-toggle", "install-shims"]
        self.assertEqual(self.steps("claude", ["agent-toggle@agent-toggle"]), [
            ["claude", "plugin", "marketplace", "update", "agent-toggle"],
            ["claude", "plugin", "update", "agent-toggle@agent-toggle"], shims])
        self.assertEqual(self.steps("claude", ["other@x"]), [shims])
        self.assertEqual(self.steps(None, []), [shims])


class CmdUpdateTest(unittest.TestCase):
    def test_hands_update_kit_the_app_identity_language_and_after_steps(self) -> None:
        with mock.patch.object(update_prompt, "after_steps", return_value=[["a"]]), \
                mock.patch.object(i18n, "LANGUAGE", "zh-TW"), \
                mock.patch.object(update_kit, "update", return_value=2) as upd:
            self.assertEqual(update_prompt.cmd_update(), 2)
        kw = upd.call_args.kwargs
        self.assertEqual((kw["app"], kw["repo"], kw["dist"], kw["lang"], kw["after"]),
                         ("agent-toggle", "weskao/agent-toggle", "agent-toggle", "zh-TW", [["a"]]))
        self.assertIs(kw["out"], sys.stderr)

    def test_steps_run_off_stdout_and_never_prompt(self) -> None:
        with mock.patch.object(update_prompt.plugin_cli, "claude_bin", return_value="/x/claude"), \
                mock.patch.object(update_prompt.subprocess, "run") as sub:
            update_prompt._run(["claude", "plugin", "update", "p"])
            update_prompt._run(["agent-toggle", "install-shims"])
        first, second = sub.call_args_list
        self.assertEqual(first.args[0], ["/x/claude", "plugin", "update", "p"])
        self.assertEqual(second.args[0], ["agent-toggle", "install-shims"])
        for call in (first, second):
            self.assertIs(call.kwargs["stdout"], sys.stderr)
            self.assertEqual(call.kwargs["stdin"], subprocess.DEVNULL)


class HintTest(unittest.TestCase):
    def hint(self, *, tty=True, enabled=True, latest="v9.9.9", json_mode=False):
        err = io.StringIO()
        with mock.patch.object(settings, "get", return_value=enabled), \
                mock.patch.object(update_kit, "should_hint", return_value=tty), \
                mock.patch.object(update_kit, "check", return_value=latest) as check, \
                mock.patch.object(sys, "stderr", err):
            update_prompt.hint(json_mode=json_mode)
        return err.getvalue(), check

    def test_prints_the_one_line_hint_on_stderr(self) -> None:
        text, check = self.hint()
        self.assertEqual(text, update_kit.hint("agent-toggle", "v9.9.9", __version__, "en") + "\n")
        self.assertEqual(check.call_args.kwargs["repo"], "weskao/agent-toggle")
        self.assertEqual(check.call_args.kwargs["current"], __version__)

    def test_silent_off_a_tty_when_disabled_under_json_or_nothing_newer(self) -> None:
        for kw in ({"tty": False}, {"enabled": False}, {"json_mode": True}, {"latest": None}):
            self.assertEqual(self.hint(**kw)[0], "", kw)

    def test_never_raises(self) -> None:
        with mock.patch.object(update_kit, "should_hint", side_effect=RuntimeError):
            update_prompt.hint()


if __name__ == "__main__":
    unittest.main()
