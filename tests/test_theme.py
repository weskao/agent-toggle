"""ui/theme: pure segment builders, glyph fallback, mono/colour mapping (fake curses)."""
from __future__ import annotations

import subprocess
import sys
import unittest
from unittest import mock

from agent_toggle.ui import theme
from agent_toggle.ui.theme import ASCII, UNICODE


def width(segments) -> int:
    return sum(theme.cell_width(text) for text, _ in segments)


def text(segments) -> str:
    return "".join(t for t, _ in segments)


class FakeCurses:
    """Just enough of curses for init_curses_colors / draw_segments."""

    A_BOLD, A_REVERSE, A_DIM = 1 << 8, 1 << 9, 1 << 10
    COLOR_BLACK, COLOR_RED, COLOR_GREEN, COLOR_YELLOW = 0, 1, 2, 3
    COLOR_BLUE, COLOR_MAGENTA, COLOR_CYAN = 4, 5, 6

    class error(Exception):
        pass

    def __init__(self, colors=True, default_colors=True, init_pair_fails=False):
        self.colors, self.default_colors, self.init_pair_fails = colors, default_colors, init_pair_fails
        self.pairs: dict[int, tuple[int, int]] = {}
        self.started = False

    def has_colors(self):
        return self.colors

    def start_color(self):
        self.started = True

    def use_default_colors(self):
        if not self.default_colors:
            raise self.error("no default colours")

    def init_pair(self, n, fg, bg):
        if self.init_pair_fails:
            raise self.error("init_pair")
        self.pairs[n] = (fg, bg)

    def color_pair(self, n):
        return n << 16


class FakeWin:
    def __init__(self, rows=3, cols=10):
        self.size, self.calls = (rows, cols), []

    def getmaxyx(self):
        return self.size

    def addstr(self, y, x, s, attr):
        self.calls.append((y, x, s, attr))


class Enc:
    def __init__(self, encoding):
        self.encoding = encoding


class ImportTest(unittest.TestCase):
    def test_importing_theme_needs_no_curses(self):
        code = ("import sys; sys.modules['curses'] = None\n"
                "import agent_toggle.ui.theme as t\n"
                "assert t.init_curses_colors.__name__")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             encoding="utf-8", check=False)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_no_module_level_curses_import(self):
        import ast
        from pathlib import Path
        tree = ast.parse(Path(theme.__file__).read_text(encoding="utf-8"))
        top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        names = {a.name for n in top for a in getattr(n, "names", [])} | {
            n.module for n in top if isinstance(n, ast.ImportFrom)}
        self.assertNotIn("curses", names)


class GlyphTest(unittest.TestCase):
    def test_unicode_when_the_stream_can_encode_it(self):
        self.assertIs(theme.get_glyphs(Enc("utf-8"), {"TERM": "xterm"}), UNICODE)

    def test_ascii_when_the_encoding_cannot(self):
        self.assertIs(theme.get_glyphs(Enc("cp1252"), {"TERM": "xterm"}), ASCII)
        self.assertIs(theme.get_glyphs(Enc("ascii"), {}), ASCII)

    def test_ascii_for_dumb_terminals_and_unknown_encodings(self):
        self.assertIs(theme.get_glyphs(Enc("utf-8"), {"TERM": "dumb"}), ASCII)
        self.assertIs(theme.get_glyphs(Enc("no-such-codec"), {}), ASCII)

    def test_ascii_set_really_is_ascii(self):
        for value in vars(ASCII).values():
            for item in (value if isinstance(value, tuple) else (value,)):
                if isinstance(item, str):
                    self.assertTrue(item.isascii(), item)


class WidthTest(unittest.TestCase):
    def test_cjk_counts_double(self):
        self.assertEqual(theme.cell_width("ab"), 2)
        self.assertEqual(theme.cell_width("設定"), 4)

    def test_truncate_by_cells_with_ellipsis(self):
        self.assertEqual(theme.truncate("abcdef", 4, "…"), "abc…")
        self.assertEqual(theme.truncate("設定選單", 5), "設定")
        self.assertEqual(theme.truncate("abc", 5, "…"), "abc")
        self.assertEqual(theme.truncate("abc", 0), "")


class ChipsTest(unittest.TestCase):
    PAIRS = [("space", "toggle"), ("q", "quit"), ("?", "help")]

    def test_roles_and_exact_width(self):
        seg = theme.key_chips(self.PAIRS, 40)
        self.assertEqual(width(seg), 40)
        self.assertEqual(text(seg).rstrip(), "space toggle  q quit  ? help")
        self.assertEqual(seg[0], ("space", "chip_key"))
        self.assertEqual(seg[1], (" toggle", "chip_label"))

    def test_chips_that_do_not_fit_are_dropped_whole(self):
        seg = theme.key_chips(self.PAIRS, 20)
        self.assertEqual(text(seg).rstrip(), "space toggle  q quit")
        self.assertEqual(width(seg), 20)
        self.assertEqual(text(theme.key_chips(self.PAIRS, 8)).strip(), "")

    def test_zero_width(self):
        self.assertEqual(theme.key_chips(self.PAIRS, 0), [])


class TabBarTest(unittest.TestCase):
    def test_active_tab_is_the_only_highlighted_one(self):
        seg = theme.tab_bar(["Harness", "Type", "Cost"], 1, 40)
        roles = {t: r for t, r in seg if t.strip() in ("Harness", "Type", "Cost")}
        self.assertEqual(roles, {" Harness ": "tab_inactive", " Type ": "tab_active",
                                 " Cost ": "tab_inactive"})
        self.assertEqual(width(seg), 40)
        self.assertIn(("│", "muted"), seg)

    def test_ascii_separator_and_clipping(self):
        seg = theme.tab_bar(["alpha", "beta"], 0, 10, ASCII)
        self.assertEqual(text(seg), " alpha | b")
        self.assertEqual(width(seg), 10)


class TitleBarTest(unittest.TestCase):
    def test_left_and_right_fill_the_row(self):
        seg = theme.title_bar("agent-toggle", "3 live", 30)
        self.assertEqual(width(seg), 30)
        self.assertTrue(text(seg).startswith("agent-toggle"))
        self.assertTrue(text(seg).endswith("3 live"))
        self.assertEqual(seg[0][1], "title")
        self.assertEqual(seg[-1], ("3 live", "muted"))

    def test_long_left_is_ellipsised_not_the_right(self):
        seg = theme.title_bar("a very long title indeed", "v1", 15)
        self.assertEqual(width(seg), 15)
        self.assertTrue(text(seg).endswith("v1"))
        self.assertIn("…", text(seg))

    def test_narrow_width_drops_the_right_side(self):
        seg = theme.title_bar("title", "right-hand", 8)
        self.assertEqual(text(seg), "title   ")

    def test_wide_characters_never_overflow(self):
        seg = theme.title_bar("設定選單設定選單", "右", 9)
        self.assertEqual(width(seg), 9)


class CostBarTest(unittest.TestCase):
    def test_empty_half_full(self):
        self.assertEqual(text(theme.cost_bar(0, 10, 8)), " " * 8)
        self.assertEqual(text(theme.cost_bar(5, 10, 8)), "████    ")
        self.assertEqual(text(theme.cost_bar(10, 10, 8)), "█" * 8)

    def test_eighth_resolution(self):
        self.assertEqual(text(theme.cost_bar(1, 8, 8)), "█" + " " * 7)
        self.assertEqual(text(theme.cost_bar(3, 64, 8)), "▍" + " " * 7)   # 3/64 of 8 cells = 3/8

    def test_over_max_clamps_and_tiny_values_show_a_sliver(self):
        self.assertEqual(text(theme.cost_bar(99, 10, 4)), "████")
        self.assertEqual(text(theme.cost_bar(0.001, 10, 4)), "▏   ")

    def test_degenerate_inputs(self):
        self.assertEqual(text(theme.cost_bar(5, 0, 4)), "    ")
        self.assertEqual(text(theme.cost_bar(-1, 10, 4)), "    ")
        self.assertEqual(theme.cost_bar(5, 10, 0), [])

    def test_ascii_fallback(self):
        self.assertEqual(text(theme.cost_bar(5, 10, 8, ASCII)), "####    ")
        self.assertEqual(text(theme.cost_bar(0.001, 10, 4, ASCII)), "#   ")
        self.assertTrue(text(theme.cost_bar(5, 10, 8, ASCII)).isascii())

    def test_filled_part_has_the_accent_role(self):
        self.assertEqual(theme.cost_bar(5, 10, 8)[0], ("████", "accent"))


class AnsiTest(unittest.TestCase):
    def test_disabled_is_verbatim(self):
        self.assertEqual(theme.ansi("live", "on", False), "on")

    def test_enabled_wraps_and_resets(self):
        self.assertEqual(theme.ansi("live", "on", True), "\x1b[32mon\x1b[0m")
        self.assertEqual(theme.ansi("on", "x", True), theme.ansi("live", "x", True))

    def test_plain_text_and_unknown_roles_and_empty_are_untouched(self):
        for role in ("text", "nonsense"):
            self.assertEqual(theme.ansi(role, "x", True), "x")
        self.assertEqual(theme.ansi("error", "", True), "")

    def test_every_styled_role_has_a_sequence(self):
        for role in theme.ROLES:
            if role != "text":
                self.assertTrue(theme.ansi(role, "x", True).startswith("\x1b["), role)

    def test_render_ansi(self):
        seg = [("a", "live"), (" b", "text")]
        self.assertEqual(theme.render_ansi(seg, False), "a b")
        self.assertEqual(theme.render_ansi(seg, True), "\x1b[32ma\x1b[0m b")


class PaletteTest(unittest.TestCase):
    def test_colour_mapping_gives_distinct_pairs_for_the_colour_roles(self):
        cur = FakeCurses()
        pal = theme.init_curses_colors(True, cur)
        self.assertFalse(pal.mono)
        self.assertTrue(cur.started)
        for role in theme.ROLES:
            self.assertIn(role, pal.attrs)
        live, pending, parked = pal.attr("live"), pal.attr("pending"), pal.attr("parked")
        self.assertEqual(len({live, pending, parked}), 3)
        self.assertEqual(cur.pairs[live >> 16], (cur.COLOR_GREEN, -1))
        self.assertEqual(cur.pairs[pending >> 16], (cur.COLOR_MAGENTA, -1))
        self.assertEqual(pal.attr("cursor"), cur.A_BOLD | cur.A_REVERSE)
        self.assertEqual(pal.attr("on"), live)
        self.assertEqual(pal.attr("changed"), pending)
        self.assertEqual(pal.attr("no-such-role"), 0)

    def test_pairs_are_shared_between_roles_of_one_colour(self):
        cur = FakeCurses()
        pal = theme.init_curses_colors(True, cur)
        self.assertEqual(pal.attr("harness") >> 16, pal.attr("choice") >> 16)
        self.assertLess(len(cur.pairs), len(theme.ROLES))

    def test_black_background_when_default_colours_are_unavailable(self):
        cur = FakeCurses(default_colors=False)
        theme.init_curses_colors(True, cur)
        self.assertTrue(all(bg == cur.COLOR_BLACK for _fg, bg in cur.pairs.values()))

    def _assert_mono(self, pal, cur):
        self.assertTrue(pal.mono)
        allowed = cur.A_BOLD | cur.A_REVERSE | cur.A_DIM
        for role in theme.ROLES:
            self.assertEqual(pal.attr(role) & ~allowed, 0, role)   # no colour-pair bits
        self.assertEqual(pal.attr("parked"), cur.A_DIM)
        self.assertEqual(pal.attr("cursor"), cur.A_REVERSE)
        self.assertEqual(pal.attr("live"), cur.A_BOLD)
        self.assertNotEqual(pal.attr("live"), pal.attr("parked"))

    def test_disabled_is_monochrome_and_never_touches_pairs(self):
        cur = FakeCurses()
        self._assert_mono(theme.init_curses_colors(False, cur), cur)
        self.assertFalse(cur.started)
        self.assertEqual(cur.pairs, {})

    def test_a_terminal_without_colour_is_monochrome(self):
        cur = FakeCurses(colors=False)
        self._assert_mono(theme.init_curses_colors(True, cur), cur)

    def test_curses_error_during_setup_falls_back_to_mono(self):
        cur = FakeCurses(init_pair_fails=True)
        self._assert_mono(theme.init_curses_colors(True, cur), cur)


class DrawTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cur = FakeCurses()
        self.pal = theme.init_curses_colors(True, self.cur)
        patcher = mock.patch.dict(sys.modules, {"curses": self.cur})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_segments_are_painted_left_to_right_with_role_attrs(self):
        win = FakeWin(rows=3, cols=12)
        end = theme.draw_segments(win, 1, 2, [("ab", "live"), ("設", "pending")], self.pal)
        self.assertEqual(win.calls, [(1, 2, "ab", self.pal.attr("live")),
                                     (1, 4, "設", self.pal.attr("pending"))])
        self.assertEqual(end, 6)

    def test_text_is_clipped_to_the_window(self):
        win = FakeWin(rows=3, cols=6)
        theme.draw_segments(win, 0, 2, [("abcdefgh", "text")], self.pal)
        self.assertEqual(win.calls[0][2], "abcd")

    def test_the_last_cell_of_the_last_row_is_left_alone(self):
        win = FakeWin(rows=3, cols=6)
        theme.draw_segments(win, 2, 0, [("abcdefgh", "text")], self.pal)
        self.assertEqual(win.calls[0][2], "abcde")

    def test_a_curses_error_stops_painting_without_raising(self):
        win = FakeWin()
        win.addstr = mock.Mock(side_effect=FakeCurses.error)
        theme.draw_segments(win, 0, 0, [("a", "text"), ("b", "text")], self.pal)
        self.assertEqual(win.addstr.call_count, 1)


if __name__ == "__main__":
    unittest.main()
