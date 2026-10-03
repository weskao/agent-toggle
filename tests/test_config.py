"""`agent-toggle config`: token in the credential store, chat id in config.json, CI secrets on stdin.

telegram-kit is an optional extra, so every test runs against FakeKit: no keychain, no network, no gh.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from unittest import mock

from test_cli_surface import CliCase

from agent_toggle import config


class FakeStore:
    items: dict[str, str] = {}

    def __init__(self, service: str) -> None:
        self.service = service

    def get(self, key: str) -> str:
        return self.items.get(key, "")

    def set(self, key: str, value: str) -> bool:
        self.items[key] = value
        return True

    def delete(self, key: str) -> bool:
        return self.items.pop(key, None) is not None


class FakeKit:
    TOKEN_KEY = "telegram_bot_token"
    CredentialStore = FakeStore

    def __init__(self, hidden: str | None = "") -> None:
        FakeStore.items = {}
        self.hidden, self.sent, self.deliver = hidden, [], True

    def resolve_credentials(self, token="", chat_id=""):
        return token, chat_id

    def read_hidden(self, prompt):
        return self.hidden

    def mask_secret(self, secret):
        return "********" + secret[-4:]

    def backend_label(self):
        return "Fake Keychain"

    def send_message(self, token, chat_id, text):
        self.sent.append((token, chat_id, text))
        return self.deliver


class ConfigTest(CliCase):
    def setUp(self) -> None:
        super().setUp()
        self.kit = FakeKit("123456:ABCDEFtoken")
        self.gh_calls: list[tuple[tuple, str | None]] = []
        for target, new in (("kit", lambda: self.kit), ("gh", self.fake_gh)):
            patcher = mock.patch.object(config, target, new)
            patcher.start()
            self.addCleanup(patcher.stop)
        which = mock.patch.object(config.shutil, "which", return_value="/usr/bin/gh")
        which.start()
        self.addCleanup(which.stop)

    def fake_gh(self, *args, stdin=None):
        self.gh_calls.append((args, stdin))
        out = "owner/repo\n" if args[:2] == ("repo", "view") else ""
        return subprocess.CompletedProcess(args, 0, out, "")

    def interactive(self, chat_id: str) -> tuple[int, str]:
        with mock.patch.object(sys.stdin, "isatty", return_value=True), \
                mock.patch("builtins.input", return_value=chat_id):
            rc, out, _ = self.run_cli("config")
        return rc, out

    def test_prompt_stores_token_and_chat_id_and_masks_the_show(self) -> None:
        rc, out = self.interactive("-100123")
        self.assertEqual(rc, 0)
        self.assertEqual(FakeStore.items["telegram_bot_token"], "123456:ABCDEFtoken")
        self.assertEqual(json.loads(config.config_file().read_text(encoding="utf-8")),
                         {"telegram_chat_id": "-100123"})
        self.assertIn("********oken", out)
        self.assertNotIn("ABCDEF", out)
        self.assertEqual(self.run_json("config")[1]["results"][1]["detail"], "-100123")

    def test_enter_keeps_and_dash_clears(self) -> None:
        self.interactive("-100123")
        self.kit.hidden = ""
        self.interactive("")
        self.assertEqual(FakeStore.items["telegram_bot_token"], "123456:ABCDEFtoken")
        self.kit.hidden = "-"
        self.interactive("-")
        self.assertEqual(FakeStore.items, {})
        self.assertEqual(load_chat(), {})

    def test_bad_chat_id_is_refused(self) -> None:
        self.assertEqual(self.interactive("not a chat")[0], 2)
        self.assertEqual(load_chat(), {})

    def test_test_message_needs_both_values_and_reports_delivery(self) -> None:
        self.assertEqual(self.run_cli("config", "test")[0], 2)
        self.interactive("-100123")
        self.assertEqual(self.run_cli("config", "test")[0], 0)
        self.assertEqual(self.kit.sent[0][:2], ("123456:ABCDEFtoken", "-100123"))
        self.kit.deliver = False
        self.assertEqual(self.run_cli("config", "test")[0], 1)

    def test_sync_ci_sends_values_on_stdin_never_in_argv(self) -> None:
        self.interactive("-100123")
        rc, env = self.run_json("config", "sync-ci")
        self.assertEqual(rc, 0, env)
        sets = [(a, s) for a, s in self.gh_calls if a[:2] == ("secret", "set")]
        self.assertEqual([s for _, s in sets], ["123456:ABCDEFtoken", "-100123"])
        self.assertEqual([a[2] for a, _ in sets], list(config.SECRETS))
        self.assertTrue(all("owner/repo" in a and "ABCDEF" not in " ".join(a) for a, _ in sets))
        self.gh_calls.clear()
        self.assertEqual(self.run_cli("config", "sync-ci", "--dry-run", "--repo", "o/r")[0], 0)
        self.assertEqual(self.gh_calls, [])

    def test_missing_extra_is_a_clear_exit_4(self) -> None:
        mock.patch.stopall()
        with mock.patch.dict(sys.modules, {"telegram_kit": None}):
            rc, _, err = self.run_cli("config")
        self.assertEqual(rc, 4)
        self.assertIn("agent-toggle[telegram]", err)


def load_chat() -> dict:
    return config.load()


if __name__ == "__main__":
    unittest.main()
