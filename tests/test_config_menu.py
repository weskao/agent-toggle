"""`agent-toggle config` with no action: the curses settings menu and its numbered fallback.

The curses loop runs against FakeWin (queued keys, recorded text) -- no terminal, no
initscr. telegram-kit is faked (FakeKit from test_config) or absent, never the real keychain.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from base import SandboxCase
from test_cli_surface import CliCase
from test_config import FakeKit, FakeStore

from agent_toggle import __version__, config, doctor, i18n, settings, undo
from agent_toggle.output import Result
from agent_toggle.ui import config_menu as cm

try:
    import curses
except ImportError:              # no curses build (Windows without windows-curses)
    curses = None

ROOT = Path(__file__).resolve().parents[1]


class FakeWin:
    """Just enough of a curses window: replays queued keys, records each frame's text."""

    def __init__(self, keys=(), size=(40, 100)):
        self.size, self.keys, self.frames = size, list(keys), []

    def getmaxyx(self):
        return self.size

    def erase(self):
        self.frames.append({})

    def addstr(self, y, x, text, attr=0):
        self.frames[-1][y] = self.frames[-1].get(y, "") + text

    def get_wch(self):
        if not self.keys:
            return None                        # a dead input: the menu quits
        key = self.keys.pop(0)
        if key is KeyboardInterrupt:
            raise KeyboardInterrupt
        return key

    def text(self, frame=-1) -> str:
        f = self.frames[frame]
        return "\n".join(f[y] for y in sorted(f))

    def refresh(self): pass
    def keypad(self, flag): pass


def to(label: str) -> list:
    """DOWN presses from the first row to the row whose English label starts with *label*."""
    n = next(i for i, r in enumerate(cm.ITEMS) if r.label().startswith(label))
    return [curses.KEY_DOWN] * n


def number(label: str) -> str:
    return str(next(i for i, r in enumerate(cm.ITEMS, 1) if r.label().startswith(label)))


class MenuCase(SandboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(setattr, i18n, "LANGUAGE", i18n.LANGUAGE)
        i18n.set_language("en")
        # base.py's AGENT_TOGGLE_UPDATE_CHECK=0 would win over the file; restored after
        for var in ("AGENT_TOGGLE_UPDATE_CHECK", "AGENT_TOGGLE_COLOR", "AGENT_TOGGLE_LANG", "AGENT_TOGGLE_DEFAULT_HARNESS",
                    "TG_BOT_TOKEN", "TG_CHAT_ID"):
            self.addCleanup(self._restore_env, var, os.environ.get(var))
            os.environ.pop(var, None)
        self.kit = FakeKit("123456:ABCDEFtoken")

    def ctx(self, telegram: bool = True) -> cm.Ctx:
        if telegram:
            return cm.Ctx(self.kit, FakeStore(config.SERVICE))
        return cm.Ctx()

    def menu(self, keys, telegram: bool = True, size=(40, 100)) -> tuple[FakeWin, int]:
        win = FakeWin(keys, size)
        rc = cm.loop(win, self.ctx(telegram))
        self.assertEqual(win.keys, [], "unused keys: the menu quit early")
        return win, rc

    def file(self) -> dict:
        return json.loads(settings.config_file().read_text(encoding="utf-8"))


@unittest.skipIf(curses is None, "curses unavailable")
class CursesMenuTest(MenuCase):
    def test_layout_groups_and_footer(self) -> None:
        win, rc = self.menu(["q"])
        self.assertEqual(rc, 0)
        text = win.text()
        for want in ("agent-toggle config", f"v{__version__}", "saves as you go", "General", "Harnesses",
                     "Picker", "Notifications", "Tools", "Check for updates", "Sync CI secrets",
                     "› Check for updates", "╭", "╰", "↑↓ move", "R reset all",
                     "found ~/.claude", "not on this machine"):
            self.assertIn(want, text)
        self.assertNotIn("q quit", text)
        keys = next(ln for ln in text.splitlines() if "↑↓ move" in ln)
        self.assertIn("q/Ctrl+C to leave · auto-save", keys)
        help_line = next(ln for ln in text.splitlines() if "look for a newer agent-toggle" in ln)
        self.assertNotIn("q/Ctrl+C", help_line)

    def test_title_puts_version_next_to_the_name(self) -> None:
        win, _ = self.menu(["q"])
        first = win.text().splitlines()[0]
        self.assertIn(f"agent-toggle config  v{__version__}", first)
        self.assertLess(first.index(f"v{__version__}"), first.index("saves as you go"))

    def test_toggle_update_check_saves_at_once(self) -> None:
        win, _ = self.menu(["\n", "q"])
        self.assertIs(self.file()["update_check"], False)
        self.assertIn("Off", win.text())
        self.menu([" ", "q"])
        self.assertIs(settings.get("update_check"), True)

    def test_cycle_color_both_ways(self) -> None:
        self.menu([*to("Color"), curses.KEY_RIGHT, "q"])
        self.assertEqual(settings.get("color"), "always")
        self.menu([*to("Color"), curses.KEY_LEFT, curses.KEY_LEFT, "q"])
        self.assertEqual(settings.get("color"), "never")

    def test_toggle_a_harness_off(self) -> None:
        self.menu([*to("codex"), "\n", "q"])
        self.assertIs(self.file()["harness.codex"], False)
        self.assertNotIn("codex", settings.enabled_harnesses())

    def test_up_wraps_to_the_last_row_skipping_headings(self) -> None:
        win, _ = self.menu([curses.KEY_UP, "q"], size=(24, 80))
        self.assertIn("› Sync CI secrets", win.text())
        win, _ = self.menu([curses.KEY_UP, curses.KEY_DOWN, "q"], size=(24, 80))
        self.assertIn("› Check for updates", win.text())

    def test_short_terminal_scrolls(self) -> None:
        win, _ = self.menu([*to("Sync CI"), "q"], size=(24, 80))
        first, last = win.text(0), win.text()
        self.assertIn("more", first)                  # rows below the fold are announced
        self.assertNotIn("Sync CI secrets", first)
        self.assertIn("Sync CI secrets", last)
        self.assertTrue(all(len(f) <= 24 for f in win.frames))

    def test_reset_row_and_reset_all_ask_first(self) -> None:
        settings.set("color", "never")
        settings.set("update_check", False)
        with open(settings.config_file(), "r+", encoding="utf-8") as fh:   # an unknown key
            data = json.load(fh)
            data["someone_elses"] = 1
            fh.seek(0)
            fh.write(json.dumps(data))
        self.menu([*to("Color"), "r", "n", "q"])
        self.assertEqual(settings.source("color"), "file")
        win, _ = self.menu([*to("Color"), "r", "y", "q"])
        self.assertIn("Reset Color to its default? (y/n)", win.text(-2))
        self.assertEqual(settings.source("color"), "default")
        self.menu(["R", "n", "q"])
        self.assertIs(settings.get("update_check"), False)
        self.menu(["R", "y", "q"])
        self.assertEqual(self.file(), {"someone_elses": 1})

    def test_quit_keys_all_exit_0(self) -> None:
        for key in ("q", "\x1b", KeyboardInterrupt):
            self.assertEqual(self.menu([key])[1], 0)

    def test_language_switch_redraws_in_zh_tw(self) -> None:
        win, _ = self.menu([*to("Language"), "\n", "q"])
        self.assertEqual(i18n.LANGUAGE, "zh-TW")
        self.assertEqual(self.file()["language"], "zh-TW")
        last = win.text()
        for want in ("繁體中文", "一般", "檢查更新", "變更即時儲存"):
            self.assertIn(want, last)
        keys = next(ln for ln in last.splitlines() if "↑↓ 移動" in ln)
        self.assertIn("q/Ctrl+C 離開 · 自動儲存", keys)
        self.assertNotIn("q 離開", last)
        self.assertNotIn("Check for updates", last)

    def test_env_override_is_tagged(self) -> None:
        os.environ["AGENT_TOGGLE_COLOR"] = "never"
        win, _ = self.menu([*to("Color"), "\n", "q"])
        line = next(ln for ln in win.text().splitlines() if "Color" in ln)
        self.assertIn("never  env", line)
        self.assertIn("AGENT_TOGGLE_COLOR is set in the environment", win.text())

    def test_failed_write_is_a_red_message_not_a_crash(self) -> None:
        with mock.patch.object(settings, "set", side_effect=OSError("disk full")):
            win, rc = self.menu(["\n", "q"])
        self.assertEqual(rc, 0)
        self.assertIn("could not save: disk full", win.text())

    def test_chat_id_inline_edit_validates(self) -> None:
        win, _ = self.menu([*to("Telegram chat"), "\n", *"abc", "\n",
                            *"\x7f\x7f\x7f", *"-100123", "\n", "q"])
        self.assertTrue(any("not a chat ID: abc" in win.text(i) for i in range(len(win.frames))))
        self.assertEqual(self.file()["telegram_chat_id"], "-100123")
        self.assertIn("chat ID set to -100123", win.text())
        # prefilled: Esc cancels, `-` then Enter clears
        self.menu([*to("Telegram chat"), "\n", "x", "\x1b", "q"])
        self.assertEqual(settings.get("telegram_chat_id"), "-100123")
        self.menu([*to("Telegram chat"), "\n", *["\x7f"] * 7, "-", "\n", "q"])
        self.assertNotIn("telegram_chat_id", self.file())

    def test_token_row_shows_mask_with_last_chars_never_the_token(self) -> None:
        FakeStore.items = {"telegram_bot_token": "123456:ABCDEFtoken"}
        ctx = self.ctx()
        self.assertEqual(ctx.token, "********oken")
        row = next(r for r in cm.ITEMS if r.kind == "secret")
        shown = lambda g: "".join(s for s, _ in cm.value_segments(ctx, row, g))
        self.assertEqual(shown(cm.theme.UNICODE), "••••••••oken")
        self.assertEqual(shown(cm.theme.ASCII), "********oken")
        self.assertNotIn("ABCDEF", shown(cm.theme.UNICODE))
        # env fallback: masked too, tagged env; unset: "not set"
        FakeStore.items = {}
        with mock.patch.object(self.kit, "resolve_credentials",
                               return_value=("999999:ZZZZwxyz", "")):
            self.assertEqual(self.ctx().token, "********wxyz")
            self.assertTrue(self.ctx().token_env)
        self.assertEqual(self.ctx().token, "")

    def test_token_edit_is_hidden_and_stored(self) -> None:
        win, _ = self.menu([*to("Telegram bot"), "\n", *"sekrit", "\n", "q"])
        self.assertEqual(FakeStore.items["telegram_bot_token"], "sekrit")
        self.assertTrue(any("••••••" in win.text(i) for i in range(len(win.frames))))
        self.assertFalse(any("sekrit" in win.text(i) for i in range(len(win.frames))))
        self.assertIn("••••", win.text())
        # an empty secret field cancels; `-` clears
        self.menu([*to("Telegram bot"), "\n", "\n", "q"])
        self.assertEqual(FakeStore.items["telegram_bot_token"], "sekrit")
        self.menu([*to("Telegram bot"), "\n", "-", "\n", "q"])
        self.assertEqual(FakeStore.items, {})

    def test_arrows_do_not_open_the_editor(self) -> None:
        win, _ = self.menu([*to("Telegram chat"), curses.KEY_RIGHT, "q"])
        self.assertNotIn("Enter saves", win.text())

    def test_unknown_term_falls_back_to_the_numbered_list(self) -> None:
        out = io.StringIO()
        with mock.patch.object(cm, "_tty", return_value=True), \
                mock.patch.object(curses, "wrapper", side_effect=curses.error("setupterm")), \
                mock.patch.object(sys, "stdin", io.StringIO("")), \
                mock.patch.object(sys, "stdout", out):
            self.assertEqual(cm.run(self.ctx()), 0)
        self.assertIn("Pick a setting to change", out.getvalue())

    def test_without_telegram_kit_the_rows_are_inert(self) -> None:
        win, rc = self.menu([*to("Telegram chat"), "\n", "r", "q"], telegram=False)
        self.assertEqual(rc, 0)
        self.assertIn("install agent-toggle[telegram]", win.text())
        self.assertFalse(settings.config_file().exists())

    def test_tools_run_with_a_fresh_result_in_a_pager(self) -> None:
        seen = []

        def fake_doctor(harness, out):
            seen.append(out)
            out.row(None, "layout", "claude", "check", "ok", "fine")
        with mock.patch.object(doctor, "cmd_doctor", side_effect=fake_doctor):
            win, _ = self.menu([*to("Health"), "\n", "x", "q"])
        self.assertEqual(len(seen), 1)
        self.assertIsInstance(seen[0], Result)
        self.assertFalse(seen[0].json_mode)
        pager = win.text(-2)
        self.assertIn("Health check (doctor)", pager)
        self.assertIn("v layout claude fine", pager)
        self.assertIn("any key returns", pager)
        self.assertIn("› Health check", win.text())       # back on the menu
        # a long output line wraps inside the box instead of being clipped
        long = "x" * 150 + "TAIL"
        with mock.patch.object(doctor, "cmd_doctor", side_effect=lambda h, out: out.say(long)):
            win, _ = self.menu([*to("Health"), "\n", "x", "q"], size=(24, 80))
        self.assertIn("TAIL", win.text(-2))
        # arrows never run a tool
        with mock.patch.object(doctor, "cmd_doctor") as m:
            self.menu([*to("Health"), curses.KEY_RIGHT, "q"])
        m.assert_not_called()

    def test_doctor_fixes_are_asked_after_its_pager(self) -> None:
        ran = []
        fix = doctor.Fix({"status": "warn"}, "Fix the thing?", ran.append)
        with mock.patch.object(doctor, "cmd_doctor", return_value=[fix]):
            self.menu([*to("Health"), "\n", "x", "n", "q"])
            self.assertEqual(ran, [])
            win, _ = self.menu([*to("Health"), "\n", "x", "y", "x", "q"])
        self.assertEqual(len(ran), 1)
        self.assertIn("fixed 1 of 1", win.text(-2))

    def test_undo_and_sync_ask_first(self) -> None:
        with mock.patch.object(undo, "cmd_undo") as m:
            self.menu([*to("Undo"), "\n", "n", "q"])
            m.assert_not_called()
            self.menu([*to("Undo"), "\n", "y", "x", "q"])
        args = m.call_args.args
        self.assertEqual(args[:2], (False, None))
        self.assertIsInstance(args[2], Result)
        with mock.patch.object(config, "sync_ci") as m:
            self.menu([*to("Sync CI"), "\n", "n", "q"])
            m.assert_not_called()
            self.menu([*to("Sync CI"), "\n", "y", "x", "q"])
        self.assertFalse(m.call_args.args[2].dry_run)
        self.assertIsNone(m.call_args.args[2].repo)

    def test_install_shims_gets_a_namespace_and_errors_show(self) -> None:
        from agent_toggle import cli
        with mock.patch.object(cli, "cmd_install_shims") as m:
            self.menu([*to("Install shims"), "\n", "x", "q"])
        ns, res = m.call_args.args
        self.assertEqual((ns.dry_run, ns.harness), (False, None))
        self.assertIsInstance(res, Result)
        # the real one with no harness installed dies with code 4: shown, not raised
        win, rc = self.menu([*to("Install shims"), "\n", "x", "q"])
        self.assertEqual(rc, 0)
        self.assertNotIn("Traceback", win.text(-2))

    def test_send_test_without_values_shows_the_error(self) -> None:
        win, _ = self.menu([*to("Send test"), "\n", "x", "q"])
        self.assertIn("set the bot token and chat id first", win.text(-2))
        self.assertEqual(self.kit.sent, [])


class NumberedTest(MenuCase):
    def run_numbered(self, text: str, telegram: bool = True) -> tuple[int, str]:
        out = io.StringIO()
        rc = cm.numbered(self.ctx(telegram), io.StringIO(text), out)
        return rc, out.getvalue()

    def test_title_shows_the_package_version(self) -> None:
        _, out = self.run_numbered("q\n")
        self.assertIn(f"agent-toggle config  v{__version__}", out.splitlines()[0])

    def test_digits_toggle_and_cycle_then_eof_quits(self) -> None:
        rc, out = self.run_numbered(f"1\n{number('Color')}\n")
        self.assertEqual(rc, 0)
        self.assertIn(f"Pick a setting to change (1-{len(cm.ITEMS)}): ", out)
        self.assertIn("q/Ctrl+C to leave · auto-save", out)
        self.assertIn(" 1. Check for updates", out)
        self.assertIs(settings.get("update_check"), False)
        self.assertEqual(settings.get("color"), "always")

    def test_q_empty_and_eof_return_0_and_bad_numbers_are_refused(self) -> None:
        for text in ("q\n", "\n", ""):
            self.assertEqual(self.run_numbered(text)[0], 0)
        rc, out = self.run_numbered("99\nq\n")
        self.assertIn("Enter one of the setting numbers", out)
        self.assertFalse(settings.config_file().exists())

    def test_odd_digits_and_a_non_utf8_pipe_never_crash(self) -> None:
        rc, out = self.run_numbered("\u00b2\nq\n")
        self.assertEqual(rc, 0)
        self.assertIn("Enter one of the setting numbers", out)
        ascii_out = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
        i18n.set_language("zh-TW")
        self.assertEqual(cm.numbered(self.ctx(), io.StringIO(""), ascii_out), 0)

    def test_zh_tw_prompts_on_an_ascii_pipe_never_crash(self) -> None:
        chat, undo_row = number("Telegram chat"), number("Undo")
        i18n.set_language("zh-TW")
        raw = io.BytesIO()
        ascii_out = io.TextIOWrapper(raw, encoding="ascii")
        with mock.patch.object(undo, "cmd_undo"):
            rc = cm.numbered(self.ctx(), io.StringIO(f"{chat}\n\n{undo_row}\nn\nq\n"), ascii_out)
        self.assertEqual(rc, 0)
        ascii_out.flush()
        self.assertIn(b"(y/n)", raw.getvalue())

    def test_a_rejected_chat_id_is_not_echoed_in_full(self) -> None:
        _, out = self.run_numbered(f"{number('Telegram chat')}\n123456:ABCDEFGHIJKLMNOP\n")
        self.assertIn("not a chat ID: 123456:AB...", out)
        self.assertNotIn("CDEFGH", out)

    def test_chat_id_prompt_and_token_via_hidden_input(self) -> None:
        self.run_numbered(f"{number('Telegram chat')}\nnope\n")
        self.assertIsNone(settings.get("telegram_chat_id"))
        _, out = self.run_numbered(f"{number('Telegram chat')}\n@my_channel\n"
                                   f"{number('Telegram bot')}\n")
        self.assertEqual(settings.get("telegram_chat_id"), "@my_channel")
        self.assertEqual(FakeStore.items["telegram_bot_token"], "123456:ABCDEFtoken")
        self.assertNotIn("ABCDEF", out)

    def test_without_telegram_kit_rows_are_degraded(self) -> None:
        rc, out = self.run_numbered(f"{number('Telegram chat')}\nq\n", telegram=False)
        self.assertEqual(rc, 0)
        self.assertIn("install agent-toggle[telegram]", out)
        self.assertFalse(settings.config_file().exists())

    def test_actions_confirm_and_print_their_output(self) -> None:
        with mock.patch.object(undo, "cmd_undo") as m:
            _, out = self.run_numbered(f"{number('Undo')}\nn\n")
            m.assert_not_called()
            self.assertIn("cancelled", out)
            self.run_numbered(f"{number('Undo')}\n")       # EOF at the y/n is a no
            m.assert_not_called()
        with mock.patch.object(doctor, "cmd_doctor",
                               side_effect=lambda h, out: out.say("all good")):
            _, out = self.run_numbered(f"{number('Health')}\n")
        self.assertIn("all good", out)

    def test_doctor_fixes_ask_y_n(self) -> None:
        ran = []
        fix = doctor.Fix({"status": "warn"}, "Fix the thing?", ran.append)
        with mock.patch.object(doctor, "cmd_doctor", return_value=[fix]):
            _, out = self.run_numbered(f"{number('Health')}\nn\n")
            self.assertEqual(ran, [])
            _, out = self.run_numbered(f"{number('Health')}\ny\n")
        self.assertEqual(len(ran), 1)
        self.assertIn("Fix the thing? (y/n)", out)
        self.assertIn("fixed 1 of 1", out)


class CliConfigMenuTest(CliCase):
    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(setattr, i18n, "LANGUAGE", i18n.LANGUAGE)

    def test_config_without_the_extra_is_not_exit_4(self) -> None:
        with mock.patch.dict(sys.modules, {"telegram_kit": None}), \
                mock.patch.object(sys, "stdin", io.StringIO("1\n")):
            rc, out, err = self.run_cli("config")
        self.assertEqual(rc, 0, err)
        self.assertIn("install agent-toggle[telegram]", out)
        self.assertIs(settings.get("update_check"), False)
        with mock.patch.dict(sys.modules, {"telegram_kit": None}):
            rc, env = self.run_json("config")
            self.assertEqual(rc, 0)
            self.assertEqual(env["results"][0]["status"], "skipped")
            keys = {r["name"]: r for r in env["results"] if r["type"] == "setting"}
            self.assertEqual(keys["update_check"]["value"], False)
            self.assertEqual(keys["color"]["source"], "default")
            self.assertEqual(self.run_cli("config", "test")[0], 4)
            self.assertEqual(self.run_cli("config", "sync-ci")[0], 4)

    def test_subprocess_with_dev_null_stdin_exits_0(self) -> None:
        env = {**os.environ, "HOME": str(self.tmp), "USERPROFILE": str(self.tmp)}
        done = subprocess.run([sys.executable, "-m", "agent_toggle", "config"],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              encoding="utf-8", env=env, cwd=str(ROOT), timeout=60, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn("Traceback", done.stderr)
        self.assertIn("Pick a setting to change", done.stdout)


if __name__ == "__main__":
    unittest.main()
