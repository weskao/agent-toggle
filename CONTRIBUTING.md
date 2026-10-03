# Contributing

Thanks for helping. agent-toggle is stdlib-only at runtime and supports
Python 3.10 and newer; please keep it that way.
The one exception is `agent-toggle config`, which imports telegram-kit from the
optional `telegram` extra; keep that import inside `agent_toggle/config.py`.

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

## Releasing

To release a new version:

1. Bump `__version__` in `agent_toggle/__init__.py` and update `CHANGELOG.md`
   under the `[Unreleased]` section. Merge these changes to `main` with a
   conventional commit subject (e.g., `chore(release): v0.2.0`).
2. Ensure CI is green on `main` before proceeding.
3. Create an annotated tag: `git tag -a vX.Y.Z -m "Release X.Y.Z"` (where
   `X.Y.Z` matches `__version__` exactly).
4. Push the tag: `git push origin vX.Y.Z`. This triggers the release workflow.
5. If reviewers are configured for the `pypi` environment, approve the
   `publish` job in the GitHub Actions UI.
6. Watch the `smoke` job in the workflow run. It installs the published version
   from PyPI on macOS and verifies a `disable` / `enable` round trip.

**Action pins:** GitHub Actions in `ci.yml` are pinned by full commit SHA with a
`# vX.Y.Z` comment that notes the semantic version. Action pins are updated
deliberately (not automatically) when you decide to upgrade.
