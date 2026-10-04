# TODO

What is left after phases 0-4 (merged to `main`, CI green on macOS, Linux and Windows;
`v0.1.0` published to PyPI). Source of truth for scope and acceptance criteria:
`docs/DESIGN.md` §8.

Finished items are `- [x]`, open ones `- [ ]`. Change an item to `- [x]` only when its
acceptance criterion holds on CI, and tick the matching box in `docs/DESIGN.md` §7/§8 in
the same commit.

## Roadmap phases

### Phase 3 — remaining harnesses

- [x] copilot: skills, agents, `mcp-config.json`
- [x] vibe: skills
- [x] devin: explicit "not applicable" row (no toggleable resources)
- [x] agy: table row now; adapter once its layout is observed (open question 1)
- [x] each harness has a fixture home under `tests/fixtures/<harness>/` and passes
      `tests/test_conformance.py`
- [x] README support matrix, `CHANGELOG.md`, shim type/harness list updated
      (`tests/test_install.py` pins the shim list to the table)

### Phase 3.5 — colorful CLI

Source: `docs/DESIGN.md` §8, Phase 3.5. Human output only; never color `--json`.

- [x] one small color helper in `agent_toggle/output.py` (stdlib ANSI): green ok, red
      error, yellow warning, cyan harness name, dim for secondary text
- [x] `--color auto|always|never` option flag; `auto` colors only on a TTY and honors
      `NO_COLOR`, `FORCE_COLOR` and `TERM=dumb`
- [x] color applied to `status`, `list`, `cost`, `disable`/`enable` results, `doctor`,
      warnings and errors
- [x] picker: curses color pairs, monochrome fallback when `curses.has_colors()` is false
- [x] Windows: enable virtual-terminal processing, or stay plain when it fails
- [x] tests: non-TTY, `--json` and `NO_COLOR=1` output has no escape bytes and equals the
      pre-3.5 output; forced-color `status` and `cost` carry the expected escapes
- [x] README documents `--color` and `NO_COLOR`; CHANGELOG entry; the two-spellings table
      in `CLAUDE.md` needs no change (option flag, not a command)

### Phase 4 — PyPI release

- [x] publish `agent-toggle` from a tag via GitHub Actions trusted publishing (OIDC,
      no stored PyPI token)
- [x] pin every workflow action by SHA
- [x] README install switches to `uv tool install agent-toggle`
- [x] tagged release installs on a clean macOS runner and passes the phase-0 smoke
      test (v0.1.0 Release run green)

### Phase 5 — Linux + Windows

- [ ] fill the platform table (`docs/DESIGN.md` §5.11) from real installs, not
      guesses (open question 4)
- [x] `windows-curses` extra and the numbered-menu fallback for the picker
      (`ui/menu.py`, `tests/test_menu.py`; green on the Windows runner)
- [x] path, case and symlink behaviour tested on CI (the Windows runner enables
      Developer Mode first, so the symlink tests run there too)
- [x] plugin ids with shell metacharacters refused on the Windows runner
      (`tests/test_platform.py`)
- [x] documented harness homes per OS (README; Linux and Windows values are from
      harness docs, not yet checked on real installs)

### Phase 6 — CI hardening + Telegram failure alerts

Model on the aicp workflow (`docs/DESIGN.md` §8.1). `.github/workflows/ci.yml` has the
hardening and the notify job; only the live forced-failure check is left.

- [x] `permissions: contents: read`
- [x] per-ref `concurrency` with `cancel-in-progress: true`
- [x] `PYTHONUTF8=1`, job `timeout-minutes`
- [x] `notify-telegram` job: `needs: test`, `failure() && push` only, reads
      `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` from secrets, exits 0 with a
      `::notice::` when unset, escapes `&`, `<`, `>`
- [x] README "CI notifications" section with the two `gh secret set` commands
- [x] `agent-toggle config [test|sync-ci]`: bot token in the OS credential store and chat id
      in `config.json` through telegram-kit (optional `telegram` extra), README Install and
      CI notifications updated (`tests/test_config.py`)
- [x] forced failure on a push sends one message (repo, branch, short SHA, run URL);
      PRs never notify; no token or chat id in the repo (verified 2026-10-04, run 37144727572)

## Security and design items still planned (`docs/DESIGN.md` §5, §6, §6.1)

Checked against the code on 2026-10-03; every §6.1 row is done.

- [x] §6.1 row 3: output audit, so `log.jsonl`, `--json`, `-v`, `--dry-run` and
      tracebacks never show backed-up values
- [x] §6.1 row 4: shim text says to act only on the user's request, and disabling a
      `rule` prints a warning that rules may carry safety constraints
- [x] §6.1 row 5: validate plugin ids against `[A-Za-z0-9._@:/-]+` (with phase 5)
- [x] §6.1 row 8: Dependabot for workflow actions, only the publish job gets
      `id-token: write`, README pins installs to a tag (`git+<repo-url>@vX.Y.Z`)
- [x] §6.1 row 9: `install-shims` refuses to overwrite a file that lacks the shim
      marker (shims written before the marker are recognised and upgraded)
- [x] `SECURITY.md`: add the scope statement and a supported-versions line
- [x] `ui/menu.py` numbered-prompt fallback for Windows (§5.10) and its scripted-stdin
      test (§9)
- [x] one shim template per harness in `agent_toggle/shims/` (§5.9): `claude.md.tmpl`
      plus `generic.md.tmpl` rendered per harness; a `<harness>.md.tmpl` overrides it
- [x] `docs/harnesses.md`, the survey table kept current (§6 Documentation)
- [x] `status` reports state entries whose `parked_at` no longer exists, with the fix
      command (§6 Stale state; shares `doctor.missing_parked`)
- [x] each harness row records the harness version it was verified against (§6
      Harness drift)
- [x] shell completion files generated at release time (§6): `tools/gen_completions.py`,
      uploaded as a release-workflow artifact

## Needs a real install to settle (`docs/DESIGN.md` §11)

- [x] OpenCode `skills.paths` default when unset (q2)
- [ ] copilot `installed-plugins/` entry format once a plugin is installed (q3)
- [x] openclaw flag shape `skills.entries.<name>.enabled` /
      `plugins.entries.<name>.enabled`, and whether `openclaw.json` is strict JSON (q5)
- [x] opencode `mcp.<name>.enabled`, and whether `opencode.json` is JSONC (q6)

## Known gaps (non-critical, `docs/DESIGN.md` §8.2)

Fix only if one bites; none blocks a phase.

- [x] `run_cli` uses a fixed 120 s timeout for every `claude plugin ...` call
      (`backends/plugin_cli.py`) -- now 30 s for read-only `plugin list`, 120 s for
      every other call, `AGENT_TOGGLE_CLI_TIMEOUT` overrides both
- [x] a plugin can show twice in the picker when parked under a name that differs from
      `name@marketplace` -- the parked row now collapses into the listed `name@marketplace` one
- [x] picker typing mode (after `/`) has no on-screen cue -- the top line now reads
      `filter: /text█` with a hint (also in the menu fallback)
- [x] `skills.paths`: `~`, `$HOME/...` not expanded; `opencode.jsonc` not read
- [x] `install-shims` writes into `opencode/skills` even when `skills.paths` redirects
- [ ] `.synced-from-*` warning repeats once per harness viewing the directory
- [x] `aliases_from` on the harness record is metadata only
- [x] `ui` and `cost` are user-scope only (no `--project`) -- both take `--project <dir>`
- [x] picker has no profile key; profiles are CLI only -- `p` stages a saved profile or
      saves the live state (curses and menu)
- [x] Python 3.10 TOML edits are checked textually, not parsed (`doctor` reports
      `unverified`) -- now parse-checked by a stdlib structural validator
- [x] JSONC / JSON5 config is refused, never rewritten -- JSONC is now rewritten in place
      (comments and trailing commas kept); JSON5 stays refused
- [x] project park across filesystems is copy + delete, not atomic; an empty
      `parked/<sha8>/*-disabled` dir can remain after `enable` -- now a crash-safe
      temp copy + rename, and `enable` prunes the empty park dirs
- [x] a killed run between a flag write and the state save leaves the flag `false`
      with no state entry -- now a write-ahead `pending` entry the next run settles
- [x] `profile save --project` lists only parked servers for a project with only
      `.mcp.json` -- its live servers are listed too
- [x] project `.mcp.json` backups hold the whole file text (mode `0600`), which can
      include other servers' auth headers -- now only the toggled entry, put back by a
      byte-preserving re-insert; old whole-file backups still restore
- [x] `doctor` does not check companion files -- parked file present, no orphans, never both live and parked

## Housekeeping

- [x] re-run `agent-toggle install-shims` after upgrading so the installed shim picks
      up `profile`, `undo` and `doctor` (done 2026-10-03, 7 harnesses)
- [x] delete the merged branches (`phase-1`..`phase-6`, `ci-telegram-setup`; local and `origin`)
