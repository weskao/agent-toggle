"""`agent-toggle config`: the settings menu, plus the Telegram settings behind the CI alerts.

With no action it opens the settings menu (`ui.config_menu`: curses on a terminal, a
numbered list otherwise; `--json` prints every setting). Same shape as aicp's `--config`:
the bot token lives only in the OS credential store (Keychain / Secret Service / DPAPI,
through telegram-kit, never in a file or an argument vector); the chat id and every other
setting are ordinary configuration in ~/.agent-toggle/config.json (`settings`).
telegram-kit is the optional `agent-toggle[telegram]` extra, imported only here: `test`
and `sync-ci` need it, the menu works without it (its Telegram rows say so).
"""
from __future__ import annotations

import shutil
import subprocess

from . import __version__, settings
from .output import CliError, Result, die

SERVICE = "agent-toggle"
CHAT_KEY = "telegram_chat_id"
SECRETS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
CHAT_ID_RE = settings.CHAT_ID_RE
INSTALL_HINT = "install the extra: uv tool install 'agent-toggle[telegram]'"


config_file = settings.config_file
load = settings.raw


def save_chat_id(chat_id: str) -> None:
    if chat_id:
        settings.set(CHAT_KEY, chat_id)
    else:
        settings.reset(CHAT_KEY)


def kit():
    try:
        import telegram_kit
    except ImportError:
        die(f"telegram-kit is not installed; {INSTALL_HINT}", 4)
    return telegram_kit


def optional_kit():
    """telegram-kit, or None without the extra (the menu degrades instead of exiting 4)."""
    try:
        return kit()
    except CliError:
        return None


def gh(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def credentials(tk, store) -> tuple[str, str]:
    """The stored values, else TG_BOT_TOKEN / TG_CHAT_ID (telegram-kit's own fallback)."""
    # settings.get, not the raw file: an invalid (maybe pasted-secret) value is never shown
    return tk.resolve_credentials(store.get(tk.TOKEN_KEY), settings.get(CHAT_KEY) or "")


def show(tk, store, out: Result) -> None:
    """Every setting as rows (the --json view); the token only ever masked."""
    if tk is None:
        out.row(None, "telegram", "bot-token", "show", "skipped", INSTALL_HINT)
        chat_id = settings.get(CHAT_KEY) or ""
    else:
        stored = store.get(tk.TOKEN_KEY)
        token, chat_id = credentials(tk, store)
        where = f"stored in {tk.backend_label()}" if stored else "from TG_BOT_TOKEN" if token else ""
        out.row(None, "telegram", "bot-token", "show", "ok" if token else "skipped",
                f"{tk.mask_secret(token)} ({where})" if token else "not set")
    out.row(None, "telegram", "chat-id", "show", "ok" if chat_id else "skipped",
            chat_id or "not set")
    for key in settings.DEFAULTS:
        if key != CHAT_KEY:
            value = settings.get(key)
            text = ("on" if value else "off") if isinstance(value, bool) else str(value)
            out.row(None, "setting", key, "show", "ok", text, value=value,
                    source=settings.source(key))
    out.row(None, "info", "version", "show", "ok", __version__, value=__version__)


def need(tk, store) -> tuple[str, str]:
    token, chat_id = credentials(tk, store)
    if not token or not chat_id:
        die("set the bot token and chat id first: agent-toggle config", 2)
    return token, chat_id


def send_test(tk, store, out: Result) -> None:
    token, chat_id = need(tk, store)
    if not tk.send_message(token, chat_id, f"{SERVICE}: test message"):
        die("the test message was not delivered (check the token and chat id, and that "
            "you pressed Start in the chat)", 1)
    out.row(None, "telegram", "test", "send", "ok", "delivered")


def sync_ci(tk, store, args, out: Result) -> None:
    """Upload the token and chat id as the repository secrets ci.yml reads (values on stdin)."""
    token, chat_id = need(tk, store)
    if not shutil.which("gh"):
        die("the GitHub CLI `gh` is not installed", 4)
    repo = args.repo or gh("repo", "view", "--json", "nameWithOwner",
                           "--jq", ".nameWithOwner").stdout.strip()
    if not repo:
        die("cannot tell the repository; pass --repo OWNER/REPO (and check `gh auth status`)", 2)
    for name, value in zip(SECRETS, (token, chat_id)):
        if args.dry_run:
            out.row(None, "secret", name, "would-set", "planned", f"on {repo}")
            continue
        done = gh("secret", "set", name, "--repo", repo, stdin=value)
        if done.returncode:
            out.row(None, "secret", name, "set", "error", done.stderr.strip() or "gh failed")
        else:
            out.row(None, "secret", name, "set", "ok", f"on {repo}")


def cmd_config(args, out: Result) -> None:
    if args.action in ("test", "sync-ci"):
        tk = kit()
        store = tk.CredentialStore(SERVICE)
        if args.action == "test":
            send_test(tk, store, out)
        else:
            sync_ci(tk, store, args, out)
        return
    tk = optional_kit()
    store = tk.CredentialStore(SERVICE) if tk is not None else None
    if out.json_mode:
        show(tk, store, out)
        return
    # lazy: config_menu imports this module
    from .ui import config_menu
    config_menu.run(config_menu.Ctx(tk, store, out.color))
