"""Styled help: `agent-toggle help` (grouped overview) and `agent-toggle help <command>`.

`help X`, `--help X` and `X --help` all render :func:`command_help`, so the three
spellings can never drift. Every string goes through i18n.t(); colour is the
caller's decision (output.use_color), applied with theme.ansi.
tests/test_help.py checks every parser option of a command appears in its help.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import __version__
from .harnesses import TYPES
from .i18n import t
from .ui.theme import ansi, cell_width

PROG = "agent-toggle"


@dataclass(frozen=True)
class Cmd:
    usage: str
    summary: str
    description: str
    args: list[tuple[str, str]] = field(default_factory=list)
    options: list[tuple[str, str]] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)


def _project() -> tuple[str, str]:
    return ("--project dir", t("help.opt.project",
                               "project scope: <dir>/.claude and <dir>/.mcp.json (`.` = cwd)"))


def _dry_run() -> tuple[str, str]:
    return ("--dry-run", t("help.opt.dry_run", "show the plan; change nothing"))


def _type_arg() -> tuple[str, str]:
    return ("<type>", " | ".join(TYPES))


def commands(default_harness: str = "claude") -> dict[str, Cmd]:
    """Every command's help, in display order (built per call: the language may change)."""
    return {
        "ui": Cmd(
            "ui|pick [--dry-run] [--project dir]",
            t("help.ui.summary", "interactive picker"),
            t("help.ui.desc", "Browse every harness's items, stage changes with the keyboard, "
                              "apply them in one batch."),
            options=[_dry_run(), _project()],
            examples=["ui", "pick --dry-run", "ui --project ."]),
        "disable": Cmd(
            "disable <type> <name>... [--dry-run] [--project dir]",
            t("help.disable.summary", "park one or more items"),
            t("help.disable.desc", "Move items out of the harness's view; nothing is deleted. "
                                   "Without --harness this acts on %s.", default_harness),
            args=[_type_arg(), ("<name>...", t("help.arg.names", "one or more item names"))],
            options=[_dry_run(), _project()],
            examples=["disable skill my-skill", "disable mcp my-server --harness codex",
                      "disable agent my-agent --project . --dry-run"]),
        "enable": Cmd(
            "enable <type> <name>... | enable --all [--dry-run] [--project dir]",
            t("help.enable.summary", "restore one or more items"),
            t("help.enable.desc", "Put parked items back. Without --harness this acts on %s; "
                                  "--all restores everything (optionally one --harness).",
              default_harness),
            args=[_type_arg(), ("<name>...", t("help.arg.names", "one or more item names"))],
            options=[("--all", t("help.opt.all", "restore every disabled item")),
                     _dry_run(), _project()],
            examples=["enable skill my-skill", "enable --all --dry-run",
                      "enable --all --harness codex"]),
        "undo": Cmd(
            "undo [--dry-run]",
            t("help.undo.summary", "reverse the last logged batch"),
            t("help.undo.desc", "Reverse the most recent disable/enable batch from the log."),
            options=[_dry_run()],
            examples=["undo --dry-run", "undo"]),
        "profile": Cmd(
            "profile save|apply|diff|list [name|file] [--out file] [--dry-run] [--project dir]",
            t("help.profile.summary", "save / apply / diff / list named sets of live items"),
            t("help.profile.desc", "A profile records which items are live, so a whole "
                                   "setup can be switched in one step."),
            args=[("save|apply|diff|list", t("help.arg.profile_action", "what to do")),
                  ("name|file", t("help.arg.profile_target", "a profile name or a JSON file"))],
            options=[("--out file", t("help.opt.out", "save: write the profile here instead")),
                     ("--dry-run", t("help.opt.profile_dry_run",
                                     "apply: show the plan; change nothing")),
                     _project()],
            examples=["profile save work", "profile diff work", "profile apply work --dry-run",
                      "profile list"]),
        "status": Cmd(
            "status",
            t("help.status.summary", "health check"),
            t("help.status.desc", "Show the state file, each installed harness and its "
                                  "parked items, and any drift worth fixing."),
            examples=["status", "status --harness codex", "status --json"]),
        "list": Cmd(
            "list [<type>] [--project dir]",
            t("help.list.summary", "what is currently disabled"),
            t("help.list.desc", "List every parked item, optionally one type or one project."),
            args=[("<type>", t("help.opt.type", "only this type: %s", " | ".join(TYPES)))],
            options=[("--project dir", t("help.opt.list_project", "only this project's entries"))],
            examples=["list", "list skill", "list --project ."]),
        "cost": Cmd(
            "cost [--type T] [--project dir]",
            t("help.cost.summary", "estimated startup tokens per item, biggest first"),
            t("help.cost.desc", "Estimate how many tokens each item loads at startup "
                                "(chars/4, +-25%), and what parked items already save."),
            options=[("--type T", t("help.opt.type", "only this type: %s", " | ".join(TYPES))),
                     ("--project dir", t("help.opt.cost_project",
                                         "price this project's .claude and .mcp.json, "
                                         "not user scope"))],
            examples=["cost", "cost --type mcp", "cost --harness codex --json"]),
        "doctor": Cmd(
            "doctor",
            t("help.doctor.summary", "check harness layouts and state"),
            t("help.doctor.desc", "Compare the state file against disk; exit 1 on problems. "
                                  "On a terminal, offers y/n for each fix it can run itself "
                                  "(chmod, stale entries, orphans, interrupted ops); "
                                  "otherwise changes nothing."),
            examples=["doctor", "doctor --harness claude"]),
        "config": Cmd(
            "config [test|sync-ci] [--repo OWNER/REPO] [--dry-run]",
            t("help.config.summary", "settings and Telegram CI alerts"),
            t("help.config.desc", "Without an action, open the settings; `test` sends one "
                                  "Telegram message, `sync-ci` sets the GitHub repo secrets."),
            args=[("test|sync-ci", t("help.arg.config_action", "optional action"))],
            options=[("--repo OWNER/REPO", t("help.opt.repo",
                                             "sync-ci: repository (default: this one)")),
                     ("--dry-run", t("help.opt.config_dry_run",
                                     "sync-ci: show the plan; set nothing"))],
            examples=["config", "config test", "config sync-ci --dry-run"]),
        "install-shims": Cmd(
            "install-shims [--dry-run] [--gitignore | --no-gitignore]",
            t("help.install_shims.summary", "write the skill shim into every installed harness"),
            t("help.install_shims.desc", "Install the agent-toggle skill so each harness's "
                                         "agent can call this tool, and report stale "
                                         "legacy `<dir>-disabled/` lines in a harness "
                                         "home's .gitignore (asked y/n on a terminal). "
                                         "On a terminal, then offers y/n for each "
                                         "problem doctor can fix."),
            options=[_dry_run(),
                     ("--gitignore", t("help.opt.gitignore",
                                       "remove stale park-dir .gitignore lines without asking")),
                     ("--no-gitignore", t("help.opt.no_gitignore",
                                          "skip the stale park-dir .gitignore check"))],
            examples=["install-shims --dry-run", "install-shims --harness codex",
                      "install-shims --gitignore"]),
        "migrate": Cmd(
            "migrate",
            t("help.migrate.summary",
              "import ~/.claude-toggle state; move legacy park dirs"),
            t("help.migrate.desc", "Import the state of the older claude-toggle tool once, "
                                   "and move legacy `<dir>-disabled/` park dirs out of the "
                                   "harness homes into ~/.agent-toggle/parked/user/. "
                                   "Safe to re-run."),
            examples=["migrate"]),
    }


def groups() -> list[tuple[str, list[str]]]:
    return [(t("help.group.toggle", "Toggle"), ["ui", "disable", "enable", "undo", "profile"]),
            (t("help.group.inspect", "Inspect"), ["status", "list", "cost", "doctor"]),
            (t("help.group.setup", "Setup"), ["config", "install-shims", "migrate"])]


def _table(rows: list[tuple[str, str]], color: bool, role: str = "choice") -> list[str]:
    width = max((cell_width(left) for left, _ in rows), default=0)
    return [f"  {ansi(role, left, color)}{' ' * (width - cell_width(left))}  {right}"
            for left, right in rows]


def _heading(text: str, color: bool) -> str:
    return ansi("accent", text.upper(), color)


def top_help(color: bool, default_harness: str = "claude") -> str:
    cmds = commands(default_harness)
    lines = [ansi("title", f"{PROG} {__version__}", color) + " — "
             + t("help.tagline", "temporarily disable / re-enable AI-agent resources "
                                 "across harnesses"),
             "", _heading(t("help.usage", "Usage"), color),
             f"  {PROG} <command> [options]"]
    for title, names in groups():
        lines += ["", _heading(title, color)]
        lines += _table([("ui, pick" if n == "ui" else n, cmds[n].summary) for n in names],
                        color)
    lines += ["", _heading(t("help.global_options", "Global options"), color)]
    lines += _table([
        ("--harness H", t("help.opt.harness", "target harness (default: %s)", default_harness)),
        ("--color auto|always|never", t("help.opt.color",
                                        "colorize output (default: auto; honors NO_COLOR)")),
        ("--json", t("help.opt.json", "print exactly one JSON document instead of text")),
        ("-v, --verbose", t("help.opt.verbose", "print a traceback on unexpected errors")),
        ("--version", t("help.opt.version", "print the version")),
        ("-h, --help", t("help.opt.help", "show help (`help <command>` for one command)")),
    ], color)
    lines += ["", _heading(t("help.examples", "Examples"), color)]
    lines += [f"  {PROG} {ex}" for ex in ("ui", "disable skill my-skill", "enable --all --dry-run",
                                         "cost --type mcp", "status --json")]
    lines += ["", _heading(t("help.exit_codes", "Exit codes"), color),
              "  " + t("help.exit_codes_text", "0 ok · 1 partial failure · 2 usage error · "
                                               "3 locked · 4 unsupported pair / harness "
                                               "not installed"),
              "", ansi("muted", t("help.footer", "Run `%s help <command>` for details · every "
                                                 "command also works with a leading --", PROG),
                       color)]
    return "\n".join(lines)


def command_help(name: str, color: bool, default_harness: str = "claude") -> str:
    """One command's help; `name` must be a key of :func:`commands` ("pick" -> "ui")."""
    name = "ui" if name == "pick" else name
    c = commands(default_harness)[name]
    lines = [ansi("title", f"{PROG} {name}", color) + " — " + c.summary,
             "", _heading(t("help.usage", "Usage"), color), f"  {PROG} {c.usage}",
             "", "  " + c.description]
    if c.args:
        lines += ["", _heading(t("help.arguments", "Arguments"), color)] + _table(c.args, color)
    if c.options:
        lines += ["", _heading(t("help.options", "Options"), color)] + _table(c.options, color)
    lines += ["", _heading(t("help.examples", "Examples"), color)]
    lines += [f"  {PROG} {ex}" for ex in c.examples]
    lines += ["", ansi("muted", t("help.global_pointer",
                                  "Global options (--harness, --json, --color, -v): "
                                  "run `%s help`", PROG), color)]
    return "\n".join(lines)


def version_help(color: bool) -> str:
    """`help version`: not a command of its own, but every spelling of it is accepted."""
    return "\n".join([_heading(t("help.usage", "Usage"), color),
                      f"  {PROG} version | --version", "",
                      "  " + t("help.opt.version", "print the version")])
