"""The logo: tiers, painter, `help` banner, and its place in the curses config menu."""
from __future__ import annotations

import io
import re
import unittest
from unittest import mock

from test_config_menu import FakeWin, MenuCase, to

from agent_toggle import settings
from agent_toggle.ui import config_menu as cm
from agent_toggle.ui import logo

try:
    import curses
except ImportError:
    curses = None

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
BIG, SMALL = logo.TIERS


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


class LogoTest(unittest.TestCase):
    def test_every_tier_row_has_its_tier_width(self) -> None:
        for rows in logo.TIERS:
            self.assertEqual({len(r) for r in rows}, {len(rows[0])})

    def test_pick_switches_at_the_declared_columns_and_rows(self) -> None:
        self.assertEqual(logo.pick(len(BIG[0]) + 1), BIG)
        self.assertEqual(logo.pick(len(BIG[0])), SMALL)        # the last cell stays empty
        self.assertEqual(logo.pick(len(SMALL[0])), ())
        self.assertEqual(logo.pick(200, len(BIG) + 1 + 30, 30), BIG)
        self.assertEqual(logo.pick(200, len(BIG) + 30, 30), SMALL)
        self.assertEqual(logo.pick(200, len(SMALL) + 30, 30), ())

    def test_colour_never_changes_the_glyphs(self) -> None:
        for mode in ("color", "mono", "animated"):
            self.assertEqual([ANSI.sub("", ln) for ln in logo.paint(BIG, mode, True)], list(BIG))
        self.assertEqual(logo.paint(SMALL, "color", False, indent=2),
                         ["  " + r for r in SMALL])

    def test_sweep_settles_on_the_static_paint_in_about_half_a_second(self) -> None:
        for rows in logo.TIERS:
            frames = logo.frames(rows)
            self.assertEqual(frames[-1], logo.paint(rows, "color", True))
            self.assertLessEqual(len(frames), logo.FRAMES + 2)
        self.assertEqual(logo.IDLE, 5.0)                       # aicp's _SHIMMER_EVERY
        lit = "38;5;231"
        self.assertTrue(any(lit in "".join(f) for f in logo.frames(SMALL)))
        for mode in ("color", "mono"):
            self.assertNotIn(lit, "".join(logo.paint(BIG, mode, True)))

    def test_banner_fits_or_stays_out(self) -> None:
        out = io.StringIO()
        logo.banner(out, "color", False, 80)                     # piped: plain glyphs
        self.assertEqual(out.getvalue(), "\n".join(SMALL) + "\n\n")
        for mode, columns, enc in (("off", 200, "utf-8"), ("color", 40, "utf-8"),
                                   ("color", 200, "ascii")):
            out = io.TextIOWrapper(io.BytesIO(), encoding=enc)
            logo.banner(out, mode, True, columns)
            out.flush()
            self.assertEqual(out.buffer.getvalue(), b"", (mode, columns, enc))
        for columns in (40, 49, 80, 97, 160):
            out = io.StringIO()
            logo.banner(out, "color", True, columns)
            self.assertTrue(all(len(ANSI.sub("", ln)) <= columns - 1
                                for ln in out.getvalue().splitlines()), columns)

    def test_animated_banner_sweeps_only_on_a_colour_tty(self) -> None:
        with mock.patch.object(logo.time, "sleep") as sleep:
            out = _Tty()
            logo.banner(out, "animated", True, 80)
            self.assertTrue(sleep.called)
            up = f"\x1b[{len(SMALL) + 1}A\r"
            self.assertEqual(out.getvalue().count(up), len(logo.frames(SMALL)))
            self.assertTrue(out.getvalue().endswith(logo.RESET))
            sleep.reset_mock()
            for out, color in ((io.StringIO(), True), (_Tty(), False)):
                logo.banner(out, "animated", color, 80)
                self.assertNotIn("A\r", out.getvalue())
            self.assertFalse(sleep.called)


@unittest.skipIf(curses is None, "curses unavailable")
class ConfigMenuLogoTest(MenuCase):
    def test_logo_sits_centred_above_the_menu_only_when_everything_fits(self) -> None:
        need = len(cm.ROWS) + 5 + len(SMALL) + 1
        xs = []
        real = FakeWin.addstr
        with mock.patch.object(FakeWin, "addstr", autospec=True,
                               side_effect=lambda w, y, x, *a: (y or xs.append(x), real(w, y, x, *a))):
            win, _ = self.menu(["q"], size=(need, 100))
        lines = win.text().splitlines()
        self.assertEqual(lines[0], SMALL[0])
        self.assertEqual(xs[0], (100 - len(SMALL[0])) // 2)         # centred over the panel
        self.assertIn("agent-toggle config", lines[len(SMALL)])     # the blank row is never written
        self.assertTrue(all(len(f) <= need for f in win.frames))
        win, _ = self.menu(["q"], size=(need - 1, 100))
        self.assertNotIn(SMALL[0], win.text())
        win, _ = self.menu(["q"], size=(need + 3, 97))
        self.assertIn(BIG[0], win.text())

    def test_logo_row_cycles_the_setting_and_off_hides_it(self) -> None:
        size = (60, 100)
        win, _ = self.menu([*to("Logo"), curses.KEY_LEFT, "q"], size=size)
        self.assertEqual(settings.get("logo"), "mono")
        win, _ = self.menu([*to("Logo"), curses.KEY_RIGHT, curses.KEY_RIGHT, "q"], size=size)
        self.assertEqual(settings.get("logo"), "off")
        self.assertNotIn(SMALL[0], win.text())

    def test_sweep_on_entry_then_again_after_idle(self) -> None:
        class Win(FakeWin):
            def timeout(self, ms): pass

            def get_wch(self):
                if self.keys and self.keys[0] == "timeout":
                    self.keys.pop(0)
                    raise curses.error
                return super().get_wch()

        win = Win(["timeout", "q"], size=(60, 100))
        menu = cm.Menu(win, self.ctx())
        menu.logo_attrs = dict.fromkeys(logo._SGR, 0)       # as if the terminal had 256 colours
        with mock.patch.object(cm.Menu, "shimmer", autospec=True) as shimmer, \
                mock.patch.object(cm.time, "monotonic", side_effect=[0, logo.IDLE, 0]):
            self.assertEqual(menu.run(), 0)
        self.assertEqual(shimmer.call_count, 2)                 # entrance + one idle

    def test_a_key_during_the_sweep_ends_it_and_is_not_lost(self) -> None:
        class Win(FakeWin):
            def timeout(self, ms): pass

        win = Win(["q"], size=(60, 100))
        menu = cm.Menu(win, self.ctx())
        menu.logo_attrs = dict.fromkeys(logo._SGR, 0)
        self.assertEqual(menu.run(), 0)                         # the "q" read mid-sweep quits
        self.assertEqual(win.keys, [])


if __name__ == "__main__":
    unittest.main()
