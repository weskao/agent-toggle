"""Every harness the table claims behaves the same way: disable then enable is a
byte-for-byte round trip, a dry run writes nothing, an unclaimed pair exits 4.

The pairs come from build(), so a new table row is covered with no test edit.
Fixture homes (tests/fixtures/<harness>/) mirror the user's HOME and hold synthetic
names only.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from test_cli_surface import CliCase, snapshot

from agent_toggle.harnesses import TYPES, build

FIXTURES = Path(__file__).parent / "fixtures"
# One synthetic item per type; codex's `prompts/` is probed as a second command dir.
ITEM = {"skill": "demo-skill", "agent": "demo-agent", "command": "demo-command",
        "rule": "demo-rule", "plugin": "demo-plugin", "mcp": "example-mcp"}
EXTRA = {("codex", "command"): "demo-prompt"}

# Claimed in the table (T4a decision) but toggle_plugin only drives the claude CLI,
# so these exit 4 today. Pinned here so closing the gap fails a test and prompts removal.
KNOWN_GAPS = {("codex", "plugin")}
PAIRS = [(n, t) for n, h in build(Path("unused")).items() for t in h.types
         if (n, t) not in KNOWN_GAPS]


def file_bytes(root: Path) -> dict[str, bytes]:
    """Every file under root (our own state dir excluded) -> content."""
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*")
            if p.is_file() and ".agent-toggle" not in p.parts}


class ConformanceTest(CliCase):
    def setUp(self) -> None:
        super().setUp()
        self.cli_rc = 0                       # stubbed claude CLI succeeds

    def load(self, harness: str) -> None:
        shutil.copytree(FIXTURES / harness, self.tmp, dirs_exist_ok=True)

    def names(self, harness: str, type_: str) -> list[str]:
        return [ITEM[type_], *([EXTRA[harness, type_]] if (harness, type_) in EXTRA else [])]

    def test_fixtures_cover_every_claimed_pair(self) -> None:
        for harness, type_ in PAIRS:
            with self.subTest(harness=harness, type=type_):
                if type_ == "plugin":         # state lives in the claude CLI, not on disk
                    continue
                self.load(harness)
                h = build(self.tmp)[harness]
                if type_ == "mcp":
                    self.assertTrue(h.mcp.file.is_file())
                else:
                    for n in self.names(harness, type_):
                        self.assertTrue(any(
                            (h.home / s / f"{n}{x}").exists()
                            for s in h.dirs[type_] for x in ("", ".md")), n)

    def test_disable_then_enable_round_trips(self) -> None:
        for harness, type_ in PAIRS:
            if type_ in ("plugin", "mcp") and harness == "claude":
                continue                      # CLI-driven; covered below
            with self.subTest(harness=harness, type=type_):
                self.load(harness)
                before = file_bytes(self.tmp)
                names = self.names(harness, type_)
                self.assertEqual(self.run_cli("disable", type_, *names,
                                              "--harness", harness)[0], 0)
                self.assertNotEqual(file_bytes(self.tmp), before)
                self.assertEqual(self.run_cli("enable", type_, *names,
                                              "--harness", harness)[0], 0)
                self.assertEqual(file_bytes(self.tmp), before)

    def test_park_dirs_come_from_the_table(self) -> None:
        self.load("claude")
        self.run_cli("disable", "rule", "demo-rule")
        self.assertTrue((self.tmp / ".claude/rules-disabled/demo-rule.md").is_file())
        self.load("codex")
        self.run_cli("disable", "command", "demo-prompt", "--harness", "codex")
        self.assertTrue((self.tmp / ".codex/prompts-disabled/demo-prompt.md").is_file())
        self.run_cli("install-shims")
        ignore = (self.tmp / ".claude/.gitignore")
        self.assertFalse(ignore.exists())      # only an existing .gitignore is touched
        ignore.write_text("")
        self.run_cli("install-shims", "--harness", "claude")
        self.assertIn("rules-disabled/", ignore.read_text().splitlines())

    def test_claude_cli_pairs_drive_the_cli(self) -> None:
        self.load("claude")
        orig = json.loads((self.tmp / ".claude.json").read_text())["mcpServers"]["example-mcp"]
        self.assertEqual(self.run_cli("disable", "plugin", "demo-plugin")[0], 0)
        self.assertEqual(self.run_cli("enable", "plugin", "demo-plugin")[0], 0)
        self.assertEqual(self.run_cli("disable", "mcp", "example-mcp")[0], 0)
        self.assertEqual(self.run_cli("enable", "mcp", "example-mcp")[0], 0)
        calls = [c for c, _ in self.cli_calls]
        self.assertEqual(calls[:3], [["plugin", "disable", "demo-plugin"],
                                     ["plugin", "enable", "demo-plugin"],
                                     ["mcp", "remove", "example-mcp", "-s", "user"]])
        self.assertEqual(calls[3][:4], ["mcp", "add-json", "--scope", "user"])
        self.assertEqual(json.loads(calls[3][5]), orig)     # restored payload is the original

    def test_dry_run_writes_nothing(self) -> None:
        for harness, type_ in PAIRS:
            with self.subTest(harness=harness, type=type_):
                self.load(harness)
                names = self.names(harness, type_)
                before = snapshot(self.tmp)
                calls = len(self.cli_calls)
                for action in ("disable", "enable"):
                    self.run_cli(action, type_, *names, "--harness", harness, "--dry-run")
                self.assertEqual(snapshot(self.tmp), before)
                self.assertEqual(len(self.cli_calls), calls)   # a dry run never shells out
                # ...and a dry-run enable of something really parked leaves it parked
                self.run_cli("disable", type_, *names, "--harness", harness)
                parked = snapshot(self.tmp)
                self.run_cli("enable", type_, *names, "--harness", harness, "--dry-run")
                self.assertEqual(snapshot(self.tmp), parked)

    def test_known_gaps_still_exit_4(self) -> None:
        for harness, type_ in KNOWN_GAPS:
            self.load(harness)
            self.assertEqual(self.run_cli("disable", type_, ITEM[type_], "--harness", harness)[0], 4)

    def test_unclaimed_pair_exits_4(self) -> None:
        for harness, h in build(self.tmp).items():
            for type_ in TYPES:
                if type_ in h.types:
                    continue
                with self.subTest(harness=harness, type=type_):
                    self.load(harness)
                    before = snapshot(self.tmp)
                    for extra in ((), ("--dry-run",)):
                        rc, _, _ = self.run_cli("disable", type_, ITEM[type_],
                                                "--harness", harness, *extra)
                        self.assertEqual(rc, 4)
                    self.assertEqual(snapshot(self.tmp), before)
