"""`agent-toggle config`: token in the credential store, chat id in config.json, CI secrets on stdin.

telegram-kit is an optional extra, so every test runs against FakeKit: no keychain, no network, no gh.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import unittest
from unittest import mock

from test_cli_surface import CliCase

from agent_toggle import __version__, config, settings
from agent_toggle.ui import config_menu


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
        """The numbered settings list (stdout is not a TTY): pick the token row (read hidden),
        then the chat id row and type *chat_id*; EOF quits."""
        rows = [r.label() for r in config_menu.ITEMS]
        token = rows.index("Telegram bot token") + 1
        chat = rows.index("Telegram chat ID") + 1
        with mock.patch.object(sys, "stdin", io.StringIO(f"{token}\n{chat}\n{chat_id}\n")):
            rc, out, _ = self.run_cli("config")
        return rc, out

    def test_prompt_stores_token_and_chat_id_and_masks_the_show(self) -> None:
        rc, out = self.interactive("-100123")
        self.assertEqual(rc, 0)
        self.assertEqual(FakeStore.items["telegram_bot_token"], "123456:ABCDEFtoken")
        self.assertEqual(json.loads(config.config_file().read_text(encoding="utf-8")),
                         {"telegram_chat_id": "-100123"})
        self.assertNotIn("ABCDEF", out)
        env = self.run_json("config")[1]
        self.assertIn("********oken", env["results"][0]["detail"])
        self.assertEqual(env["results"][1]["detail"], "-100123")
        self.assertNotIn("ABCDEF", json.dumps(env))

    def test_enter_keeps_and_dash_clears(self) -> None:
        self.interactive("-100123")
        self.kit.hidden = ""
        self.interactive("")
        self.assertEqual(FakeStore.items["telegram_bot_token"], "123456:ABCDEFtoken")
        self.assertEqual(load_chat(), {"telegram_chat_id": "-100123"})
        self.kit.hidden = "-"
        self.interactive("-")
        self.assertEqual(FakeStore.items, {})
        self.assertEqual(load_chat(), {})

    def test_bad_chat_id_is_refused(self) -> None:
        rc, out = self.interactive("not a chat")
        self.assertEqual(rc, 0)                 # a menu message, not a fatal exit
        self.assertIn("not a chat ID: not a chat", out)
        self.assertEqual(load_chat(), {})

    def test_an_invalid_chat_id_in_the_file_is_never_shown(self) -> None:
        settings.config_file().parent.mkdir(parents=True, exist_ok=True)
        settings.config_file().write_text(json.dumps({"telegram_chat_id": "123456:PASTEDsecret"}),
                                          encoding="utf-8")
        rc, out, err = self.run_cli("config", "--json")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["results"][1]["detail"], "not set")
        self.assertNotIn("PASTED", out + err)

    def test_json_lists_every_setting_with_its_source(self) -> None:
        rc, env = self.run_json("config")
        self.assertEqual(rc, 0)
        names = [r["name"] for r in env["results"] if r["type"] == "setting"]
        self.assertEqual(names, [k for k in settings.DEFAULTS if k != config.CHAT_KEY])
        sources = {r["name"]: r["source"] for r in env["results"] if r["type"] == "setting"}
        # tests/base.py sets AGENT_TOGGLE_UPDATE_CHECK=0 for the whole run
        self.assertEqual(sources.pop("update_check"), "env")
        self.assertEqual(set(sources.values()), {"default"})

    def test_json_includes_the_package_version(self) -> None:
        rc, env = self.run_json("config")
        self.assertEqual(rc, 0)
        row = next(r for r in env["results"] if r["name"] == "version")
        self.assertEqual((row["type"], row["action"], row["status"], row["detail"]),
                         ("info", "show", "ok", __version__))
        self.assertEqual(row["value"], __version__)

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

    def test_missing_extra_is_a_clear_exit_4_for_test_and_sync_ci_only(self) -> None:
        mock.patch.stopall()
        with mock.patch.dict(sys.modules, {"telegram_kit": None}):
            for action in ("test", "sync-ci"):
                rc, _, err = self.run_cli("config", action)
                self.assertEqual(rc, 4)
                self.assertIn("agent-toggle[telegram]", err)
            with mock.patch.object(sys, "stdin", io.StringIO("")):   # never the real TTY
                self.assertEqual(self.run_cli("config")[0], 0)  # the menu degrades instead


def load_chat() -> dict:
    return config.load()


if __name__ == "__main__":
    unittest.main()
