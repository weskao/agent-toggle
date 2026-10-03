"""`agent-toggle config`: the Telegram settings behind the CI failure alerts.

Same shape as aicp's `--config`: the bot token lives only in the OS credential store
(Keychain / Secret Service / DPAPI, through telegram-kit, never in a file or an
argument vector); the chat id is ordinary configuration in ~/.agent-toggle/config.json.
telegram-kit is the optional `agent-toggle[telegram]` extra, imported only here, so
every other command stays stdlib-only.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import fs
from .output import Result, die

SERVICE = "agent-toggle"
CHAT_KEY = "telegram_chat_id"
SECRETS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
CHAT_ID_RE = re.compile(r"-?\d{1,20}|@[A-Za-z0-9_]{5,64}")
INSTALL_HINT = "install the extra: uv tool install 'agent-toggle[telegram]'"


def config_file() -> Path:
    return fs.state_dir() / "config.json"


def load() -> dict:
    try:
        data = json.loads(config_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_chat_id(chat_id: str) -> None:
    data = load()
    if chat_id:
        data[CHAT_KEY] = chat_id
    else:
        data.pop(CHAT_KEY, None)
    fs.private_dir(fs.state_dir())
    fs.atomic_write(config_file(), json.dumps(data, indent=2) + "\n")


def kit():
    try:
        import telegram_kit
    except ImportError:
        die(f"telegram-kit is not installed; {INSTALL_HINT}", 4)
    return telegram_kit


def gh(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def credentials(tk, store) -> tuple[str, str]:
    """The stored values, else TG_BOT_TOKEN / TG_CHAT_ID (telegram-kit's own fallback)."""
    return tk.resolve_credentials(store.get(tk.TOKEN_KEY), str(load().get(CHAT_KEY, "")))


def show(tk, store, out: Result) -> None:
    stored = store.get(tk.TOKEN_KEY)
    token, chat_id = credentials(tk, store)
    where = f"stored in {tk.backend_label()}" if stored else "from TG_BOT_TOKEN" if token else ""
    out.row(None, "telegram", "bot-token", "show", "ok" if token else "skipped",
            f"{tk.mask_secret(token)} ({where})" if token else "not set")
    out.row(None, "telegram", "chat-id", "show", "ok" if chat_id else "skipped",
            chat_id or "not set")


def prompt(tk, store, out: Result) -> None:
    """Enter keeps the current value, `-` clears it (aicp's rule)."""
    token = tk.read_hidden("Telegram bot token (hidden; Enter keeps, - clears): ")
    if token is None:
        die("cannot hide input on this terminal; set TG_BOT_TOKEN in the environment instead", 2)
    if token == "-":
        store.delete(tk.TOKEN_KEY)
        out.say("bot token cleared")
    elif token:
        if not store.set(tk.TOKEN_KEY, token):
            die("no OS credential store here, so the token was not stored; "
                "set TG_BOT_TOKEN in the environment instead", 1)
        out.say(f"bot token stored as {tk.mask_secret(token)}")
    chat_id = input("Telegram chat id (number, -100... or @channel; Enter keeps, - clears): ").strip()
    if chat_id == "-":
        save_chat_id("")
        out.say("chat id cleared")
    elif chat_id:
        if not CHAT_ID_RE.fullmatch(chat_id):
            die(f"not a chat id: {chat_id!r} (a number such as -100123 or an @channel)", 2)
        save_chat_id(chat_id)
        out.say(f"chat id set to {chat_id}")


def need(tk, store) -> tuple[str, str]:
    token, chat_id = credentials(tk, store)
    if not token or not chat_id:
        die("set the bot token and chat id first: agent-toggle config", 2)
    return token, chat_id


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
    tk = kit()
    store = tk.CredentialStore(SERVICE)
    if args.action == "test":
        token, chat_id = need(tk, store)
        if not tk.send_message(token, chat_id, f"{SERVICE}: test message"):
            die("the test message was not delivered (check the token and chat id, and that "
                "you pressed Start in the chat)", 1)
        out.row(None, "telegram", "test", "send", "ok", "delivered")
    elif args.action == "sync-ci":
        sync_ci(tk, store, args, out)
    else:
        if not out.json_mode and sys.stdin.isatty():
            prompt(tk, store, out)
        show(tk, store, out)
