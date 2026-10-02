# Contributing

Thanks for helping. agent-toggle is stdlib-only at runtime and supports
Python 3.10 and newer; please keep it that way.

## Adding a harness

A harness is data, not code. You need three things:

1. **One table row** in `agent_toggle/harnesses.py` declaring the harness
   home, and for each supported resource type its location and mechanism
   (move, flag, remove-with-backup, or native CLI). Unsupported types are
   omitted so they fail loudly.
2. **A fixture home** under `tests/fixtures/<harness>/` that mirrors the real
   layout with synthetic names (`demo-skill`, `example-mcp`) and fake values
   (`Bearer test-token-000`). Never copy real files from your own machine.
3. **A verified-harness-version note**: state in the pull request, and in the
   row's comment, which harness version you checked the layout against.

The shared conformance test picks up the new fixture automatically.

## Tests and lint

```sh
python3 -m unittest discover -s tests
uvx ruff check .
```

Tests must run against a temporary `HOME` and never touch real harness
directories or invoke a real harness CLI.

## Versioning

We follow [SemVer](https://semver.org/):

- A state-schema version bump is a **minor** release and ships with an
  automatic migration of existing state.
- Removing a CLI flag is a **major** release.
- Everything else user-visible is recorded in `CHANGELOG.md` under
  `[Unreleased]`.

## Pull requests

Use conventional commit subjects (`feat(scope): ...`, `fix(scope): ...`),
keep changes focused, and update `README.md` when behaviour changes.
