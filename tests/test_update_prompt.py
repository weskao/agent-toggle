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
