"""The update prompt flushes typeahead around cbreak mode."""
from __future__ import annotations

import io
import sys
import unittest
from unittest import mock

from agent_toggle import update_check, update_prompt
from agent_toggle.ui import theme


class ReleaseNotesTest(unittest.TestCase):
    def test_a_v_prefixed_version_is_not_doubled_in_the_url(self) -> None:
        for latest in ("10.0", "v10.0"):
            found = update_check.UpdateAvailable(latest=latest, current="1.0")
            text = "\n".join(update_prompt._lines(found, 1, False, theme.ASCII))
            self.assertIn("/releases/tag/v10.0", text, latest)


class UpgradeStepsTest(unittest.TestCase):
    def steps(self, claude, plugins):
        with mock.patch.object(update_prompt.plugin_cli, "claude_bin", return_value=claude), \
                mock.patch.object(update_prompt.plugin_cli, "list_plugins",
                                  return_value=[{"id": i} for i in plugins]):
            return update_prompt.upgrade_steps()

    def test_the_plugin_is_updated_only_when_it_is_installed(self) -> None:
        cli, shims = update_prompt.UPGRADE, ["agent-toggle", "install-shims"]
        self.assertEqual(self.steps("claude", ["agent-toggle@agent-toggle"]), [
            cli, ["claude", "plugin", "marketplace", "update", "agent-toggle"],
            ["claude", "plugin", "update", "agent-toggle@agent-toggle"], shims])
        self.assertEqual(self.steps("claude", ["other@x"]), [cli, shims])
        self.assertEqual(self.steps(None, []), [cli, shims])


class CmdUpdateTest(unittest.TestCase):
    def run_update(self, latest):
        out = mock.Mock()
        found = update_check.UpdateAvailable("0.1", latest) if latest else None
        with mock.patch.object(update_check, "check", return_value=found) as check, \
                mock.patch.object(update_prompt, "upgrade_steps", return_value=[["a", "b"], ["c"]]), \
                mock.patch.object(update_prompt, "cache_path", return_value="x"):
            update_prompt.cmd_update(out)
        return out, check

    def test_a_newer_release_lists_every_command_and_ignores_cache_and_skip(self) -> None:
        out, check = self.run_update("0.2")
        row = out.row.call_args.kwargs
        self.assertEqual((row["latest"], row["commands"]), ("0.2", ["a b", "c"]))
        self.assertEqual((check.call_args.kwargs["ttl_seconds"], check.call_args.kwargs["ignore_skip"]), (0, True))

    def test_nothing_newer_lists_no_command(self) -> None:
        out, _ = self.run_update(None)
        self.assertEqual(out.row.call_args.kwargs["commands"], [])


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
