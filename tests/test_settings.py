"""The typed settings store over ~/.agent-toggle/config.json."""
from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import unittest
from unittest import mock

from base import SandboxCase

from agent_toggle import config, harnesses, settings

ENV_VARS = ("AGENT_TOGGLE_UPDATE_CHECK", "AGENT_TOGGLE_COLOR", "AGENT_TOGGLE_LANG",
            "AGENT_TOGGLE_DEFAULT_HARNESS")


class SettingsCase(SandboxCase):
    def setUp(self) -> None:
        super().setUp()
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for var in ENV_VARS:
            os.environ.pop(var, None)
        settings._warned.clear()
        self.addCleanup(settings._warned.clear)

    def put(self, text: str) -> None:
        path = settings.config_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def disk(self) -> dict:
        return json.loads(settings.config_file().read_text(encoding="utf-8"))

    def stderr(self, fn, *args):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            result = fn(*args)
        return result, buf.getvalue()


class SettingsTest(SettingsCase):
    def test_defaults_and_harness_names_come_from_the_table(self) -> None:
        self.assertEqual(settings.HARNESS_NAMES, tuple(harnesses.harnesses()))
        got = settings.all()
        self.assertIs(got["update_check"], True)
        self.assertEqual((got["color"], got["default_harness"], got["picker_sort"],
                          got["picker_harness"], got["picker_type"], got["language"]),
                         ("auto", "claude", "name", "all", "all", "en"))
        self.assertIsNone(got["telegram_chat_id"])
        self.assertTrue(all(got[f"harness.{n}"] is True for n in settings.HARNESS_NAMES))
        self.assertEqual(settings.source("color"), "default")

    def test_precedence_env_over_file_over_default(self) -> None:
        self.assertEqual(settings.get("color"), "auto")
        settings.set("color", "never")
        self.assertEqual((settings.get("color"), settings.source("color")), ("never", "file"))
        os.environ["AGENT_TOGGLE_COLOR"] = "always"
        self.assertEqual((settings.get("color"), settings.source("color")), ("always", "env"))
        os.environ["AGENT_TOGGLE_UPDATE_CHECK"] = "0"
        self.assertIs(settings.get("update_check"), False)
        os.environ["AGENT_TOGGLE_LANG"] = "zh-TW"
        os.environ["AGENT_TOGGLE_DEFAULT_HARNESS"] = "codex"
        self.assertEqual((settings.get("language"), settings.get("default_harness")),
                         ("zh-TW", "codex"))

    def test_invalid_file_and_env_values_fall_back_and_warn_once(self) -> None:
        self.put(json.dumps({"color": "purple", "update_check": "yes", "picker_sort": "name"}))
        os.environ["AGENT_TOGGLE_LANG"] = "klingon"
        _, err = self.stderr(lambda: (settings.get("color"), settings.get("color"),
                                      settings.get("update_check"), settings.get("language")))
        self.assertEqual(settings.get("color"), "auto")
        self.assertIs(settings.get("update_check"), True)
        self.assertEqual(settings.get("language"), "en")
        self.assertIn('agent-toggle: ignoring color="purple" (expected one of auto', err)
        self.assertIn('agent-toggle: ignoring update_check="yes" (expected a JSON boolean)', err)
        self.assertIn("agent-toggle: ignoring AGENT_TOGGLE_LANG=klingon (", err)
        self.assertEqual(err.count("ignoring color="), 1)

    def test_a_deeply_nested_file_is_corrupt_not_a_traceback(self) -> None:
        from agent_toggle import cli
        self.put("[" * 200000)
        self.assertEqual(settings.raw(), {})
        os.environ["AGENT_TOGGLE_UPDATE_CHECK"] = "0"      # setUp popped it: no real pypi.org traffic
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["list"]), 0)

    def test_corrupt_or_non_object_file_falls_back_without_raising(self) -> None:
        for text in ("{not json", "[1, 2]", ""):
            self.put(text)
            self.assertEqual(settings.get("color"), "auto")
            self.assertEqual(settings.enabled_harnesses(), list(settings.HARNESS_NAMES))
        settings.set("color", "never")          # a write recovers the file
        self.assertEqual(self.disk(), {"color": "never"})

    def test_invalid_chat_id_warning_does_not_echo_the_value(self) -> None:
        self.put(json.dumps({"telegram_chat_id": 12345678}))
        _, err = self.stderr(settings.get, "telegram_chat_id")
        self.assertIn("ignoring telegram_chat_id (invalid value)", err)
        self.assertNotIn("12345678", err)

    def test_set_validates_before_writing(self) -> None:
        for key, bad in (("color", "purple"), ("update_check", "maybe"), ("language", 3),
                         ("telegram_chat_id", "not a chat"), ("nope", "x"),
                         ("default_harness", "emacs")):
            with self.assertRaises(ValueError, msg=key):
                settings.set(key, bad)
        self.assertFalse(settings.config_file().exists())
        self.assertIs(settings.set("update_check", "off"), False)
        self.assertEqual(self.disk(), {"update_check": False})   # JSON bool, not "off"

    def test_unknown_keys_survive_every_write(self) -> None:
        self.put(json.dumps({"telegram_chat_id": "-100123", "future_key": {"a": [1]}}))
        settings.set("color", "never")
        settings.reset("color")
        settings.set("harness.codex", False)
        config.save_chat_id("@mychannel")
        settings.reset_all()
        self.assertEqual(self.disk(), {"future_key": {"a": [1]}})
        self.put(json.dumps({"telegram_chat_id": "-100123", "future_key": 1}))
        settings.set("language", "zh-TW")
        self.assertEqual(self.disk(),
                         {"telegram_chat_id": "-100123", "future_key": 1, "language": "zh-TW"})

    def test_reset_and_reset_all_remove_known_keys_only(self) -> None:
        settings.set("color", "never")
        settings.set("picker_sort", "cost")
        settings.reset("color")
        self.assertEqual(self.disk(), {"picker_sort": "cost"})
        settings.reset("color")                 # already absent: no-op
        settings.reset_all()
        self.assertEqual(self.disk(), {})
        with self.assertRaises(ValueError):
            settings.reset("nope")

    @unittest.skipIf(os.name == "nt", "POSIX file modes")
    def test_file_and_dir_are_private(self) -> None:
        settings.set("color", "never")
        self.assertEqual(stat.S_IMODE(settings.config_file().stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(settings.config_file().parent.stat().st_mode), 0o700)

    def test_enabled_harnesses_honours_the_file(self) -> None:
        settings.set("harness.codex", False)
        settings.set("harness.vibe", "false")
        names = settings.enabled_harnesses()
        self.assertNotIn("codex", names)
        self.assertNotIn("vibe", names)
        self.assertIn("claude", names)
        self.assertEqual(names, [n for n in settings.HARNESS_NAMES if n not in ("codex", "vibe")])

    def test_chat_id_is_shared_with_config(self) -> None:
        config.save_chat_id("-100123")
        self.assertEqual(settings.get("telegram_chat_id"), "-100123")
        self.assertEqual(settings.source("telegram_chat_id"), "file")
        self.assertEqual(config.load(), {"telegram_chat_id": "-100123"})
        config.save_chat_id("")
        self.assertIsNone(settings.get("telegram_chat_id"))
        self.assertIs(config.CHAT_ID_RE, settings.CHAT_ID_RE)


if __name__ == "__main__":
    unittest.main()
