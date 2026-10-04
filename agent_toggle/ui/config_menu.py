"""`agent-toggle config` with no action: the settings menu, in curses or as a numbered list.

Same model as aicp's `--config`: the menu is a flat tuple of rows (data, not a switch
statement), and every change is saved the moment it is made (`settings.set` / `reset`),
so quitting has nothing to roll back. The curses screen is drawn with `ui.theme`; the
numbered fallback (no TTY, no curses) prints the same rows and treats EOF as quit, so CI
never blocks. curses is imported only inside the curses code path, so the fallback still
works where curses does not exist (Windows without windows-curses).

The Telegram rows need the optional telegram-kit extra; without it they say so and do
nothing, and the rest of the menu works as usual.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import sys
from dataclasses import dataclass
from typing import Callable

from .. import __version__, config, doctor, fs, harnesses, i18n, settings, undo
from ..i18n import t
from ..output import CliError, Result, use_color
from . import theme
from .theme import Segment, cell_width

MAX_INPUT = 256          # an inline field stops growing here (a paste-flood backstop)


@dataclass(frozen=True)
class Row:
    kind: str                                   # heading | bool | choice | text | secret | action
    label: Callable[[], str]
    help: Callable[[], str] = lambda: ""
    key: str = ""                               # the settings key (bool / choice / text)
    run: Callable[[Ctx, Result], None] | None = None     # action rows
    confirm: Callable[[], str] | None = None    # y/n question asked before the action runs
    telegram: bool = False                      # inert without the telegram extra
    harness: str = ""                           # harness rows: the name, for the detected note


def _shims(ctx: Ctx, res: Result) -> None:
    # lazy: cli imports config, which imports this module
    from ..cli import cmd_install_shims
    cmd_install_shims(argparse.Namespace(dry_run=False, harness=None), res)


def _harness_row(name: str) -> Row:
    return Row("bool", lambda: name, key=f"harness.{name}", harness=name,
               help=lambda: t("config.help_harness",
                              "On: agent-toggle manages %s. Off: it is left out.", name))


ROWS: tuple[Row, ...] = (
    Row("heading", lambda: t("config.group_general", "General")),
    Row("bool", lambda: t("config.update_check", "Check for updates"), key="update_check",
        help=lambda: t("config.help_update_check",
                       "On: look for a newer agent-toggle in the background.")),
    Row("choice", lambda: t("config.color", "Color"), key="color",
        help=lambda: t("config.help_color",
                       "auto: color on a terminal only. --color and NO_COLOR still win.")),
    # Named in its own script in both languages, so the row stays findable right after
    # switching to a language the user cannot read.
    Row("choice", lambda: t("config.language", "Language / 語言"), key="language",
        help=lambda: t("config.help_language", "Language of every message.")),
    Row("choice", lambda: t("config.default_harness", "Default harness"), key="default_harness",
        help=lambda: t("config.help_default_harness",
                       "The harness a command acts on when --harness is not given.")),
    Row("heading", lambda: t("config.group_harnesses", "Harnesses")),
    *(_harness_row(n) for n in settings.HARNESS_NAMES),
    Row("heading", lambda: t("config.group_picker", "Picker")),
    Row("choice", lambda: t("config.picker_sort", "Default sort"), key="picker_sort",
        help=lambda: t("config.help_picker_sort", "The order the picker opens in: name or cost.")),
    Row("choice", lambda: t("config.picker_harness", "Default harness filter"),
        key="picker_harness",
        help=lambda: t("config.help_picker_harness", "The harness filter the picker opens with.")),
    Row("choice", lambda: t("config.picker_type", "Default type filter"), key="picker_type",
        help=lambda: t("config.help_picker_type", "The type filter the picker opens with.")),
    Row("heading", lambda: t("config.group_notifications", "Notifications")),
    Row("secret", lambda: t("config.bot_token", "Telegram bot token"), telegram=True,
        help=lambda: t("config.help_bot_token",
                       "Kept in the OS credential store, never in a file. "
                       "Typed hidden; - then Enter clears.")),
    Row("text", lambda: t("config.chat_id", "Telegram chat ID"), key="telegram_chat_id",
        telegram=True,
        help=lambda: t("config.help_chat_id",
                       "A number such as -100123 or an @channel. - then Enter clears.")),
    Row("heading", lambda: t("config.group_tools", "Tools")),
    Row("action", lambda: t("config.doctor", "Health check (doctor)"),
        run=lambda ctx, res: doctor.cmd_doctor(None, res),
        help=lambda: t("config.help_doctor",
                       "Read-only check of harness layouts and state against disk.")),
    Row("action", lambda: t("config.shims", "Install shims"), run=_shims,
        help=lambda: t("config.help_shims",
                       "Write the agent-toggle skill into every installed harness "
                       "that is on.")),
    Row("action", lambda: t("config.undo", "Undo last change"),
        run=lambda ctx, res: undo.cmd_undo(False, None, res),
        confirm=lambda: t("config.confirm_undo", "Reverse the last logged batch?"),
        help=lambda: t("config.help_undo", "Reverse the last logged disable/enable batch.")),
    Row("action", lambda: t("config.send_test", "Send test message"), telegram=True,
        run=lambda ctx, res: config.send_test(ctx.tk, ctx.store, res),
        help=lambda: t("config.help_send_test", "Send one Telegram message with the saved values.")),
    Row("action", lambda: t("config.sync_ci", "Sync CI secrets"), telegram=True,
        run=lambda ctx, res: config.sync_ci(ctx.tk, ctx.store,
                                            argparse.Namespace(repo=None, dry_run=False), res),
        confirm=lambda: t("config.confirm_sync", "Upload the token and chat ID as repo secrets?"),
        help=lambda: t("config.help_sync_ci",
                       "Set TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID on this GitHub repo (gh).")),
)
ITEMS = tuple(r for r in ROWS if r.kind != "heading")      # what the numbered list numbers


class Ctx:
    """What the rows read: telegram-kit (or None), its store, the colour mode, and a
    per-redraw snapshot of the settings. The token's presence is cached because every
    lookup can be a keychain round-trip."""

    def __init__(self, tk=None, store=None, color: str = "auto") -> None:
        self.tk, self.store, self.color_mode = tk, store, color
        self.detected = {n: h.home for n, h in harnesses.harnesses().items() if h.home.is_dir()}
        self.recolor = False
        self.refresh_token()
        self.refresh()

    def refresh(self) -> None:
        self.values = settings.all()
        self.env = {k for k, s in settings.DEFAULTS.items()
                    if s.env and settings.source(k) == "env"}

    def refresh_token(self) -> None:
        self.token = self.token_env = False
        if self.tk is not None:
            stored = self.store.get(self.tk.TOKEN_KEY)
            self.token = bool(stored or config.credentials(self.tk, self.store)[0])
            self.token_env = self.token and not stored

    def use_color(self, stream) -> bool:
        mode = self.color_mode if self.color_mode != "auto" else settings.get("color")
        return use_color(stream, mode)


# -------------------------------------------------------------- shared logic

def _after_write(ctx: Ctx, key: str | None) -> None:
    """Language and colour take effect live; key None = everything changed."""
    if key in ("language", None):
        i18n.set_language(settings.get("language"))
    if key in ("color", None):
        ctx.color_mode = settings.get("color")  # a live choice beats the --color it opened with
        ctx.recolor = True


def _env_note(key: str) -> str:
    if settings.source(key) == "env":
        return t("config.env_wins", "saved, but %s is set in the environment and still wins",
                 settings.DEFAULTS[key].env)
    return t("config.saved", "saved")


def step(ctx: Ctx, row: Row, delta: int) -> str:
    """Toggle a bool row or move a choice row by *delta*; saved at once."""
    spec = settings.DEFAULTS[row.key]
    now = settings.get(row.key)
    if spec.kind == "bool":
        new = not now
    else:
        new = spec.choices[(spec.choices.index(now) + delta) % len(spec.choices)]
    settings.set(row.key, new)
    _after_write(ctx, row.key)
    return _env_note(row.key)


def commit(ctx: Ctx, row: Row, text: str) -> str:
    """Save a typed value (``-`` clears). ValueError rejects it with the reason."""
    if row.kind == "secret":
        if text == "-":
            ctx.store.delete(ctx.tk.TOKEN_KEY)
            msg = t("config.token_cleared", "bot token cleared")
        elif ctx.store.set(ctx.tk.TOKEN_KEY, text):
            msg = t("config.token_stored", "bot token stored")
        else:
            raise ValueError(t("config.no_store", "no OS credential store here, so the token was "
                                               "not stored; set TG_BOT_TOKEN instead"))
        ctx.refresh_token()
        return msg
    if text == "-":
        config.save_chat_id("")
        return t("config.chat_id_cleared", "chat ID cleared")
    if not settings.CHAT_ID_RE.fullmatch(text):
        raise ValueError(t("config.bad_chat_id",      # cut: it may be a mis-pasted token
                           "not a chat ID: %s (a number such as -100123 or an @channel)",
                           theme.truncate(text, 12, "...")))
    config.save_chat_id(text)
    return t("config.chat_id_set", "chat ID set to %s", text)


def reset_row(ctx: Ctx, row: Row) -> str:
    if row.kind == "secret":
        return commit(ctx, row, "-")
    settings.reset(row.key)
    _after_write(ctx, row.key)
    return t("config.reset_done", "%s reset to its default", row.label())


def reset_all(ctx: Ctx) -> str:
    """Every setting back to its default; the bot token (in the keychain) stays."""
    settings.reset_all()
    _after_write(ctx, None)
    return t("config.reset_all_done", "all settings reset to their defaults")


def guarded(fn: Callable[..., str], *args) -> Segment:
    """(message, role): a failed write is a red message, never a crash of the menu."""
    try:
        return fn(*args), "live"
    except ValueError as e:
        return str(e), "error"
    except (OSError, CliError) as e:
        return t("config.save_failed", "could not save: %s", getattr(e, "msg", e)), "error"


def run_action(ctx: Ctx, row: Row) -> list[Segment]:
    """Run a Tools row with a FRESH Result; its human output becomes the overlay text."""
    res = Result("config", json_mode=False, color="never")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            row.run(ctx, res)
        except CliError as e:
            res.error(e.msg)
        except KeyboardInterrupt:
            print(t("config.interrupted", "interrupted"))
        except Exception as e:                  # an overlay message, never a dead menu
            print(t("config.action_crashed", "error: %s: %s", type(e).__name__, e))
    lines = buf.getvalue().splitlines() or [t("config.no_output", "(no output)")]
    return [(ln, "error" if ln.lstrip().startswith(("x ", "error")) else "text") for ln in lines]


def needs_extra() -> str:
    return t("config.needs_extra", "install agent-toggle[telegram]")


def value_segments(ctx: Ctx, row: Row, g: theme.Glyphs) -> list[Segment]:
    """The value column plus its dim tags, as theme segments (curses and ANSI alike)."""
    if row.telegram and ctx.tk is None:
        return [(needs_extra(), "warning")]
    if row.kind == "action":
        return [(t("config.value_run", "run"), "muted")]
    if row.kind == "secret":
        if not ctx.token:
            return [(t("config.not_set", "not set"), "muted")]
        segs = [("••••" if g.unicode else "****", "choice")]
        return segs + [("  " + t("config.tag_env", "env"), "muted")] if ctx.token_env else segs
    value = ctx.values[row.key]
    if row.kind == "bool":
        segs = [(t("config.on", "On"), "live") if value else (t("config.off", "Off"), "muted")]
    elif row.kind == "text":
        segs = [(value, "choice") if value else (t("config.not_set", "not set"), "muted")]
    elif row.key == "language":
        segs = [("繁體中文" if value == "zh-TW" else "English", "choice")]
    else:
        segs = [(str(value), "choice")]
    if row.key in ctx.env:
        segs.append(("  " + t("config.tag_env", "env"), "muted"))
    if row.harness:
        home = ctx.detected.get(row.harness)
        segs.append(("  " + (t("config.detected", "found %s", _tilde(home)) if home else
                             t("config.not_detected", "not on this machine")), "muted"))
    return segs


def _tilde(path) -> str:
    try:
        return "~/" + path.relative_to(fs.home()).as_posix()
    except ValueError:
        return str(path)


def label_width(limit: int) -> int:
    return min(limit, max(cell_width(r.label()) for r in ITEMS))


# -------------------------------------------------------------- numbered fallback

def _read(stdin) -> str | None:
    """One line, or None at EOF / an unreadable stdin (CI never blocks)."""
    try:
        line = stdin.readline()
    except (OSError, ValueError, KeyboardInterrupt):
        return None
    return line.rstrip("\r\n") if line else None


def numbered(ctx: Ctx, stdin=None, stdout=None) -> int:
    """Typed-choice surface: a number toggles / cycles / edits / runs that row."""
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    g = theme.get_glyphs(stdout)

    def say(segs: list[Segment]) -> None:
        print(theme.encodable(theme.render_ansi(segs, ctx.use_color(stdout)), stdout), file=stdout)

    while True:
        ctx.refresh()
        lw = label_width(40)
        say([(t("config.title", "agent-toggle config"), "title"), (f"  v{__version__}", "muted")])
        n = 0
        for row in ROWS:
            if row.kind == "heading":
                say([])
                say([(row.label(), "title")])
                continue
            n += 1
            label = row.label()
            say([(f"  {n:>2}. {label}{' ' * (lw - cell_width(label))}  ", "text"),
                 *value_segments(ctx, row, g)])
        say([])
        stdout.write(theme.encodable(t("config.prompt", "Pick a setting to change (1-%s, q to quit): ",
                                  len(ITEMS)), stdout))
        stdout.flush()
        choice = _read(stdin)
        if choice is None:
            print(file=stdout)                  # EOF: end the prompt line
            return 0
        if choice.strip().lower() in ("", "q"):
            return 0
        choice = choice.strip()
        if choice.isascii() and choice.isdigit() and 1 <= int(choice) <= len(ITEMS):
            for seg in _numbered_pick(ctx, ITEMS[int(choice) - 1], stdin, stdout):
                say([("  ", "text"), seg])
        else:
            say([(t("config.bad_number", "Enter one of the setting numbers shown above."),
                  "error")])
        say([])


def _numbered_pick(ctx: Ctx, row: Row, stdin, stdout) -> list[Segment]:
    if row.telegram and ctx.tk is None:
        return [(needs_extra(), "warning")]
    if row.kind in ("bool", "choice"):
        return [guarded(step, ctx, row, 1)]
    if row.kind == "secret":
        text = ctx.tk.read_hidden(t("config.prompt_token",
                                    "Telegram bot token (hidden; Enter keeps, - clears): "))
        if text is None:
            return [(t("config.no_hidden", "cannot hide input on this terminal; "
                                        "set TG_BOT_TOKEN in the environment instead"), "error")]
        return [guarded(commit, ctx, row, text.strip())] if text.strip() else []
    if row.kind == "text":
        stdout.write(theme.encodable(t("config.prompt_chat_id",
                                       "Telegram chat ID (Enter keeps, - clears): "), stdout))
        stdout.flush()
        text = (_read(stdin) or "").strip()
        return [guarded(commit, ctx, row, text)] if text else []
    if row.confirm:
        stdout.write(theme.encodable(f"{row.confirm()} {t('config.yes_no', '(y/n)')} ", stdout))
        stdout.flush()
        if (_read(stdin) or "").strip().lower() not in ("y", "yes"):
            return [(t("config.cancelled", "cancelled"), "muted")]
    return run_action(ctx, row)


# -------------------------------------------------------------- curses menu

def _boxed(segs: list[Segment], w: int, g: theme.Glyphs) -> list[Segment]:
    return [(g.v, "muted"), *theme._fit(segs, w - 2), (g.v, "muted")]


def _rule(left: str, right: str, w: int, g: theme.Glyphs, note: str = "") -> list[Segment]:
    segs = [(left + g.h, "muted")] + ([(f" {note} ", "muted")] if note else [])
    return [*theme._fit(segs + [(g.h * w, "muted")], w - 1), (right, "muted")]


def _row_segments(ctx: Ctx, row: Row, selected: bool, lw: int, g: theme.Glyphs,
                  editing: str | None) -> list[Segment]:
    if row.kind == "heading":
        return [(" ", "text"), (row.label(), "title"), (" ", "text"), (g.h * 500, "muted")]
    label = row.label()
    segs = [(f" {g.pointer} " if selected else "   ", "accent" if selected else "text"),
            (label + " " * max(0, lw - cell_width(label)), "cursor" if selected else "text"),
            ("  ", "text")]
    if editing is None:
        return segs + value_segments(ctx, row, g)
    shown = ("•" if g.unicode else "*") * len(editing) if row.kind == "secret" else editing
    return segs + [(shown, "pending"), (g.full, "pending")]


def screen(ctx: Ctx, cur: int, top: int, h: int, w: int, g: theme.Glyphs,
           msg: Segment = ("", "text"), editing: str | None = None) -> list[list[Segment]]:
    """The whole menu as one segment list per screen line (pure: no curses)."""
    body = max(1, h - 5)
    lines = [theme.title_bar(t("config.title", "agent-toggle config"),
                             f"{t('config.saves', 'saves as you go')}  v{__version__}", w, g),
             _rule(g.tl, g.tr, w, g, t("config.more", "%s more", top) if top else "")]
    lw = label_width(max(8, (w - 2) // 2 - 4))
    for i in range(top, min(len(ROWS), top + body)):
        lines.append(_boxed(_row_segments(ctx, ROWS[i], i == cur, lw, g,
                                          editing if i == cur else None), w, g))
    lines += [_boxed([], w, g)] * (body + 2 - len(lines))
    more = len(ROWS) - top - body
    lines.append(_rule(g.bl, g.br, w, g, t("config.more", "%s more", more) if more > 0 else ""))
    text, role = msg if msg[0] else (ROWS[cur].help(), "muted")
    lines.append(theme._fit([(" " + text, role)], w))
    arrows = ("↑↓", "←→/Space", "⏎") if g.unicode else ("Up/Down", "Left/Right/Space", "Enter")
    lines.append(theme.key_chips([
        (arrows[0], t("config.key_move", "move")), (arrows[1], t("config.key_change", "change")),
        (arrows[2], t("config.key_run", "change/run")), ("r", t("config.key_reset", "reset")),
        ("R", t("config.key_reset_all", "reset all")), ("q", t("config.key_quit", "quit"))], w - 1, g))
    return lines


def _wrap(text: str, width: int) -> list[str]:
    """*text* cut into pieces of at most *width* cells (CJK-safe); never an empty list."""
    parts = []
    while theme.cell_width(text) > width > 0:
        head = theme.truncate(text, width)
        parts.append(head)
        text = text[len(head):]
    return parts + [text]


def _move(cur: int, delta: int) -> int:
    """The next selectable row in *delta*'s direction, wrapping, skipping headings."""
    for _ in range(len(ROWS)):
        cur = (cur + delta) % len(ROWS)
        if ROWS[cur].kind != "heading":
            return cur
    return cur


def _scroll(cur: int, top: int, body: int) -> int:
    if cur < top:
        top = cur - 1 if cur and ROWS[cur - 1].kind == "heading" else cur
    elif cur >= top + body:
        top = cur - body + 1
    return max(0, min(top, len(ROWS) - body))


class Menu:
    def __init__(self, win, ctx: Ctx) -> None:
        import curses
        self.curses, self.win, self.ctx = curses, win, ctx
        self.g = theme.get_glyphs(sys.stdout)
        self.palette = theme.init_curses_colors(ctx.use_color(sys.stdout))
        self.cur, self.top = _move(0, 1), 0
        self.enter = ("\n", "\r", curses.KEY_ENTER)

    def draw(self, msg: Segment = ("", "text"), editing: str | None = None) -> None:
        self.ctx.refresh()
        if self.ctx.recolor:
            self.palette = theme.init_curses_colors(self.ctx.use_color(sys.stdout))
            self.ctx.recolor = False
        h, w = self.win.getmaxyx()
        self.top = _scroll(self.cur, self.top, max(1, h - 5))
        self.paint(screen(self.ctx, self.cur, self.top, h, w, self.g, msg, editing))

    def paint(self, lines: list[list[Segment]]) -> None:
        self.win.erase()
        for y, segs in enumerate(lines):
            theme.draw_segments(self.win, y, 0, segs, self.palette)
        self.win.refresh()

    def key(self):
        """The next key; Ctrl-C reads as "\\x03" and a dead input as None (quit)."""
        try:
            return self.win.get_wch()
        except KeyboardInterrupt:
            return "\x03"
        except self.curses.error:
            return None

    def ask(self, question: str) -> bool:
        self.draw((f"{question} {t('config.yes_no', '(y/n)')}", "warning"))
        return self.key() in ("y", "Y")

    def edit(self, row: Row) -> Segment:
        """Type the value into the row itself. A secret opens EMPTY (prefilling would show
        it), so Enter on an empty secret cancels and clearing takes a typed ``-``."""
        seed = "" if row.kind == "secret" else (self.ctx.values[row.key] or "")
        buf, msg = seed, (t("config.edit_hint", "type, Enter saves, Esc cancels, - then Enter "
                                             "clears"), "muted")
        while True:
            self.draw(msg, buf)
            key = self.key()
            if key in ("\x1b", "\x03", None):
                return "", "text"
            if key in self.enter:
                text = buf.strip()
                if not text and row.kind == "secret" or row.kind != "secret" and text == seed:
                    return "", "text"
                msg = guarded(commit, self.ctx, row, text or "-")
                if msg[1] != "error":
                    return msg
            elif key in (self.curses.KEY_BACKSPACE, "\x7f", "\b"):
                buf = buf[:-1]
            elif isinstance(key, str) and key.isprintable() and len(buf) < MAX_INPUT:
                buf += key

    def pager(self, title: str, lines: list[Segment]) -> None:
        """An overlay box with the action's output; ↑↓ scroll, any other key returns."""
        c, g, off = self.curses, self.g, 0
        while True:
            h, w = self.win.getmaxyx()
            body = max(1, h - 3)
            wrapped = [(part, role) for text, role in lines for part in _wrap(text, w - 3)]
            off = max(0, min(off, len(wrapped) - body))
            shown = [_boxed([(" " + text, role)], w, g) for text, role in wrapped[off:off + body]]
            more = len(wrapped) > body
            hint = t("config.pager_hint", "any key returns") + (
                "  " + t("config.pager_scroll", "Up/Down scroll") if more else "")
            self.paint([_rule(g.tl, g.tr, w, g, title), *shown,
                        *[_boxed([], w, g)] * (body - len(shown)),
                        _rule(g.bl, g.br, w, g), theme._fit([(" " + hint, "muted")], w)])
            key = self.key()
            if key == c.KEY_RESIZE:
                continue
            if more and key in (c.KEY_UP, c.KEY_DOWN, c.KEY_PPAGE, c.KEY_NPAGE):
                off += {c.KEY_UP: -1, c.KEY_DOWN: 1, c.KEY_PPAGE: -body}.get(key, body)
                continue
            return

    def activate(self, row: Row, key) -> Segment:
        c = self.curses
        if row.telegram and self.ctx.tk is None:
            return needs_extra(), "warning"
        if row.kind in ("bool", "choice"):
            return guarded(step, self.ctx, row, -1 if key == c.KEY_LEFT else 1)
        if key in (c.KEY_LEFT, c.KEY_RIGHT):
            return "", "text"                       # arrows never open an editor or run a tool
        if row.kind in ("text", "secret"):
            return self.edit(row)
        if row.confirm and not self.ask(row.confirm()):
            return t("config.cancelled", "cancelled"), "muted"
        self.draw((t("config.running", "running..."), "muted"))
        self.pager(row.label(), run_action(self.ctx, row))
        return "", "text"

    def run(self) -> int:
        c, msg = self.curses, ("", "text")
        while True:
            self.draw(msg)
            key, msg, row = self.key(), ("", "text"), ROWS[self.cur]
            if key in ("q", "Q", "\x1b", "\x03", None):
                return 0
            if key == c.KEY_UP:
                self.cur = _move(self.cur, -1)
            elif key == c.KEY_DOWN:
                self.cur = _move(self.cur, 1)
            elif key in (c.KEY_LEFT, c.KEY_RIGHT, " ", *self.enter):
                msg = self.activate(row, key)
            elif key == "r":
                if row.kind == "action":
                    msg = t("config.nothing_to_reset", "not a setting: nothing to reset"), "muted"
                elif row.telegram and self.ctx.tk is None:
                    msg = needs_extra(), "warning"
                elif self.ask(t("config.confirm_reset", "Reset %s to its default?", row.label())):
                    msg = guarded(reset_row, self.ctx, row)
            elif key == "R":
                if self.ask(t("config.confirm_reset_all", "Reset ALL settings to their defaults?")):
                    msg = guarded(reset_all, self.ctx)
            # KEY_RESIZE and anything else: just redraw


def loop(win, ctx: Ctx) -> int:
    import curses
    with contextlib.suppress(curses.error):
        curses.curs_set(0)
    with contextlib.suppress(curses.error, AttributeError):
        curses.set_escdelay(25)                     # Esc quits now, not after a second
    win.keypad(True)
    return Menu(win, ctx).run()


def curses_available() -> bool:
    try:
        import curses  # noqa: F401
    except ImportError:
        return False
    return True


def _tty(stream) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def run(ctx: Ctx) -> int:
    """The curses menu on a real terminal, else the numbered list."""
    i18n.set_language(settings.get("language"))
    if _tty(sys.stdin) and _tty(sys.stdout) and curses_available():
        import curses
        try:
            return curses.wrapper(loop, ctx)
        except curses.error:                    # e.g. an unknown TERM: no screen at all
            pass
    return numbered(ctx)
