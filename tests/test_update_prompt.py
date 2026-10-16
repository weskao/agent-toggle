"""The update prompt (panel / hint after a command) and `update` / `--update` through update_kit."""
from __future__ import annotations

import io
import subprocess
import sys
import unittest
from unittest import mock

import base  # noqa: F401 - its update_kit guard: no test lists tags or installs for real
import update_kit

from agent_toggle import i18n, update_check, update_prompt
from agent_toggle.ui import theme


class ReleaseNotesTest(unittest.TestCase):
    def test_a_v_prefixed_version_is_not_doubled_in_the_url(self) -> None:
        for latest in ("10.0", "v10.0"):
            found = update_check.UpdateAvailable(latest=latest, current="1.0")
            text = "\n".join(update_prompt._lines(found, 1, False, theme.ASCII))
            self.assertIn("/releases/tag/v10.0", text, latest)


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


class OfferTest(unittest.TestCase):
    """What the panel's answer does: Update now is `update` (update_kit, newest tag)."""

    def offer(self, answer):
        found = update_check.UpdateAvailable(current="1.0", latest="2.0")
        with mock.patch.object(update_check, "collect", return_value=found), \
                mock.patch.object(update_prompt, "is_interactive", return_value=True), \
                mock.patch.object(update_prompt.fs, "WIN", False), \
                mock.patch.object(update_prompt, "ask", return_value=answer), \
                mock.patch.object(update_prompt, "cache_path", return_value=mock.Mock()), \
                mock.patch.object(update_check, "skip") as skip, \
                mock.patch.object(update_prompt, "cmd_update", return_value=0) as upd:
            return update_prompt.offer(mock.Mock()), upd, skip

    def test_update_now_installs_the_newest_tag_through_update_kit(self) -> None:
        answer, upd, _ = self.offer(update_check.UPDATE_NOW)
        self.assertEqual(answer, update_check.UPDATE_NOW)
        upd.assert_called_once_with()

    def test_skip_and_skip_version_install_nothing(self) -> None:
        for answer in (update_check.SKIP, update_check.SKIP_VERSION):
            got, upd, skip = self.offer(answer)
            self.assertEqual(got, answer)
            upd.assert_not_called()
            self.assertEqual(skip.called, answer == update_check.SKIP_VERSION, answer)

    def test_a_failing_update_never_raises(self) -> None:
        found = update_check.UpdateAvailable(current="1.0", latest="2.0")
        with mock.patch.object(update_check, "collect", return_value=found), \
                mock.patch.object(update_prompt, "is_interactive", return_value=True), \
                mock.patch.object(update_prompt.fs, "WIN", False), \
                mock.patch.object(update_prompt, "ask", return_value=update_check.UPDATE_NOW), \
                mock.patch.object(update_prompt, "cmd_update", side_effect=RuntimeError), \
                mock.patch.object(sys, "stderr", io.StringIO()) as err:
            self.assertEqual(update_prompt.offer(mock.Mock()), update_check.UPGRADE_FAILED)
        self.assertIn("did not finish", err.getvalue())

    def test_off_a_terminal_the_hint_names_the_update_command(self) -> None:
        found = update_check.UpdateAvailable(current="1.0", latest="2.0")
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            self.assertEqual(update_prompt.hint(found), update_check.SKIP)
        self.assertEqual(err.getvalue(), "agent-toggle 2.0 is available (you have 1.0)\n"
                                         "  agent-toggle update\n")


class CmdUpdateTest(unittest.TestCase):
    def test_hands_update_kit_the_app_identity_language_and_after_steps(self) -> None:
        with mock.patch.object(update_prompt, "after_steps", return_value=[["a"]]), \
                mock.patch.object(i18n, "LANGUAGE", "zh-TW"), \
                mock.patch.object(update_kit, "update", return_value=2) as upd:
            self.assertEqual(update_prompt.cmd_update(), 2)
            kw = upd.call_args.kwargs
            after = list(kw["after"])
        self.assertEqual((kw["app"], kw["repo"], kw["dist"], kw["lang"], after),
                         ("agent-toggle", "weskao/agent-toggle", "agent-toggle", "zh-TW", [["a"]]))
        self.assertIs(kw["out"], sys.stderr)

    def test_plugin_steps_are_not_computed_unless_the_install_reads_them(self) -> None:
        failed = subprocess.CompletedProcess([], 1)
        for tag in (None, "v999.0.0"):              # the check fails / the install fails
            with mock.patch.object(update_prompt, "after_steps", return_value=[["a"]]) as steps, \
                    mock.patch.object(update_kit, "install_kind", return_value=("uv", "/x")), \
                    mock.patch.object(update_kit, "_IS_WINDOWS", False), \
                    mock.patch.object(update_kit, "latest_tag",
                                      **({"return_value": tag} if tag else
                                         {"side_effect": update_kit.UpdateError("offline")})), \
                    mock.patch.object(update_prompt.subprocess, "run", return_value=failed) as run, \
                    mock.patch.object(sys, "stderr", io.StringIO()):
                self.assertEqual(update_prompt.cmd_update(), 1, tag)
            steps.assert_not_called()
            self.assertEqual(run.call_count, 1 if tag else 0, tag)   # only the (stubbed) install

    def test_the_lazy_steps_compute_once_on_first_read(self) -> None:
        with mock.patch.object(update_prompt, "after_steps", return_value=[["a"], ["b"]]) as steps:
            lazy = update_prompt._LazySteps()
            self.assertEqual([list(lazy), list(lazy), len(lazy)], [[["a"], ["b"]], [["a"], ["b"]], 2])
        steps.assert_called_once()

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
            self.assertEqual(call.kwargs["env"]["AGENT_TOGGLE_UPDATE_CHECK"], "0")


class RunEnvTest(unittest.TestCase):
    def test_an_env_from_update_kit_is_merged_not_duplicated(self) -> None:
        with mock.patch.object(update_prompt.subprocess, "run") as sub:
            update_prompt._run(["uv", "tool", "install"], cwd="/h", env={"X": "1"})
        env = sub.call_args.kwargs["env"]
        self.assertEqual((env["X"], env["AGENT_TOGGLE_UPDATE_CHECK"]), ("1", "0"))


class FlushTest(unittest.TestCase):
    def test_input_is_flushed_after_entering_and_after_leaving_cbreak(self) -> None:
        termios = mock.MagicMock()
        calls: list[str] = []
        termios.tcgetattr.return_value = []
        termios.tcflush.side_effect = lambda *_a: calls.append("flush")
        termios.tcsetattr.side_effect = lambda *_a: calls.append("restore")
        tty = mock.MagicMock()
        tty.setcbreak.side_effect = lambda *_a: calls.append("cbreak")
        stdin = mock.MagicMock()
        stdin.fileno.return_value = 0
        found = update_check.UpdateAvailable(current="1.0", latest="2.0")
        with (mock.patch.dict(sys.modules, {"termios": termios, "tty": tty}),
              mock.patch.object(sys, "stdin", stdin), mock.patch.object(sys, "stderr", io.StringIO()),
              mock.patch.object(update_prompt, "_posix_keys", lambda _fd: iter(["quit"]))):
            self.assertEqual(update_prompt.ask(found), update_check.SKIP)
        self.assertEqual(calls, ["cbreak", "flush", "restore", "flush"])


if __name__ == "__main__":
    unittest.main()
