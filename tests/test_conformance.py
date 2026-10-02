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

from agent_toggle.backends.flag_json import read_flag
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
                if type_ == "plugin" and harness == "claude":   # state lives in the claude CLI
                    continue
                self.load(harness)
                h = build(self.tmp)[harness]
                if type_ in h.flags and (harness, type_) != ("openclaw", "skill"):
                    rel, pointer = h.flags[type_]
                    self.assertTrue(read_flag(h.home / rel, tuple(
                        ITEM[type_] if p == "<name>" else p for p in pointer)))
                elif type_ == "mcp":
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
        ignore.write_text("", encoding="utf-8")
        self.run_cli("install-shims", "--harness", "claude")
        self.assertIn("rules-disabled/", ignore.read_text(encoding="utf-8").splitlines())

    def test_claude_cli_pairs_drive_the_cli(self) -> None:
        self.load("claude")
        orig = json.loads((self.tmp / ".claude.json").read_text(encoding="utf-8"))["mcpServers"]["example-mcp"]
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


class FlagMechanismTest(CliCase):
    """openclaw plugin/skill and opencode mcp flip one `enabled` token (ASSUMED shapes)."""

    FILES = {"openclaw": ".openclaw/openclaw.json", "opencode": ".config/opencode/opencode.json"}

    def setUp(self) -> None:
        super().setUp()
        self.cli_rc = 0
        for h in ("openclaw", "opencode"):
            shutil.copytree(FIXTURES / h, self.tmp, dirs_exist_ok=True)

    def raw(self, harness: str) -> bytes:
        return (self.tmp / self.FILES[harness]).read_bytes()

    def state(self) -> dict:
        return json.loads((self.tmp / ".agent-toggle/state.json").read_text(encoding="utf-8"))

    def test_plugin_flips_only_the_flag_and_restores_byte_identically(self) -> None:
        before = self.raw("openclaw")
        self.assertEqual(self.run_cli("disable", "plugin", "demo-plugin", "--harness", "openclaw")[0], 0)
        after = self.raw("openclaw")
        self.assertEqual(after, before.replace(b'"enabled":true,"config"',
                                               b'"enabled":false,"config"'))   # one token
        entry = self.state()["disabled"]["openclaw:plugin:demo-plugin"]
        self.assertEqual(entry["mechanism"], "flag")
        self.assertEqual(entry["flag"]["was"], True)
        self.assertEqual(entry["flag"]["pointer"], ["plugins", "entries", "demo-plugin", "enabled"])
        self.assertNotIn("connector", entry)
        self.assertEqual(self.run_cli("enable", "plugin", "demo-plugin", "--harness", "openclaw")[0], 0)
        self.assertEqual(self.raw("openclaw"), before)
        self.assertEqual(self.state()["disabled"], {})

    def test_skill_with_an_entry_uses_the_flag_not_the_dir(self) -> None:
        before = file_bytes(self.tmp)
        self.assertEqual(self.run_cli("disable", "skill", "demo-skill", "--harness", "openclaw")[0], 0)
        self.assertTrue((self.tmp / ".openclaw/skills/demo-skill/SKILL.md").is_file())  # not moved
        self.assertEqual(self.state()["disabled"]["openclaw:skill:demo-skill"]["mechanism"], "flag")
        self.assertEqual(self.run_cli("enable", "skill", "demo-skill", "--harness", "openclaw")[0], 0)
        self.assertEqual(file_bytes(self.tmp), before)

    def test_skill_without_an_entry_still_moves_the_dir(self) -> None:
        d = self.tmp / ".openclaw/skills/plain-skill"
        d.mkdir()
        (d / "SKILL.md").write_text("x", encoding="utf-8")
        before = file_bytes(self.tmp)
        self.assertEqual(self.run_cli("disable", "skill", "plain-skill", "--harness", "openclaw")[0], 0)
        self.assertTrue((self.tmp / ".openclaw/skills-disabled/plain-skill/SKILL.md").is_file())
        self.assertEqual(self.state()["disabled"]["openclaw:skill:plain-skill"]["mechanism"], "move")
        self.assertEqual(self.run_cli("enable", "skill", "plain-skill", "--harness", "openclaw")[0], 0)
        self.assertEqual(file_bytes(self.tmp), before)

    def test_opencode_mcp_round_trip(self) -> None:
        before = self.raw("opencode")
        self.assertEqual(self.run_cli("disable", "mcp", "example-mcp", "--harness", "opencode")[0], 0)
        self.assertIn(b'"enabled":  false', self.raw("opencode"))
        self.assertEqual(self.run_cli("enable", "mcp", "example-mcp", "--harness", "opencode")[0], 0)
        self.assertEqual(self.raw("opencode"), before)
        self.assertEqual(self.cli_calls, [])                         # never the claude CLI

    def test_missing_entry_and_already_disabled_are_error_rows(self) -> None:
        rc, out, _ = self.run_cli("disable", "plugin", "no-such", "--harness", "openclaw", "--json")
        self.assertEqual(rc, 1)
        self.assertIn("not found", out)
        self.run_cli("disable", "plugin", "demo-plugin", "--harness", "openclaw")
        before = self.raw("openclaw")
        rc, out, _ = self.run_cli("disable", "plugin", "demo-plugin", "--harness", "openclaw")
        self.assertEqual(rc, 1)
        self.assertEqual(self.raw("openclaw"), before)

    def test_jsonc_is_refused_untouched(self) -> None:
        f = self.tmp / self.FILES["opencode"]
        f.write_text('{ // note\n "mcp": {"example-mcp": {"enabled": true}}}', encoding="utf-8")
        before = f.read_bytes()
        rc, out, _ = self.run_cli("disable", "mcp", "example-mcp", "--harness", "opencode")
        self.assertEqual(rc, 1)
        self.assertEqual(f.read_bytes(), before)
        self.assertEqual(self.state()["disabled"], {})

    def test_enable_refuses_tampered_flag_entries(self) -> None:
        self.run_cli("disable", "plugin", "demo-plugin", "--harness", "openclaw")
        disabled = self.raw("openclaw")
        sf = self.tmp / ".agent-toggle/state.json"
        good = json.loads(sf.read_text(encoding="utf-8"))
        victim = self.tmp / "victim.json"
        victim.write_text('{"plugins": {"entries": {"demo-plugin": {"enabled": false}}}}', encoding="utf-8")
        key = "openclaw:plugin:demo-plugin"
        cases = {
            "file outside home": lambda f: f.update(file=str(victim)),
            "file dotdot": lambda f: f.update(file=str(self.tmp / ".openclaw/../victim.json")),
            "other pointer": lambda f: f.update(pointer=["meta", "enabled"]),
            "was not bool": lambda f: f.update(was="yes"),
        }
        for label, mutate in cases.items():
            with self.subTest(label):
                st = json.loads(json.dumps(good))
                mutate(st["disabled"][key]["flag"])
                sf.write_text(json.dumps(st), encoding="utf-8")
                rc, out, _ = self.run_cli("enable", "plugin", "demo-plugin", "--harness", "openclaw")
                self.assertEqual(rc, 1)
                self.assertIn("refused", out)
                self.assertEqual(self.raw("openclaw"), disabled)
                self.assertEqual(victim.read_text(encoding="utf-8").count("false"), 1)

    def test_tampered_entries_that_dodge_the_mechanism_field_refuse_not_crash(self) -> None:
        self.run_cli("disable", "mcp", "example-mcp", "--harness", "opencode")
        disabled = self.raw("opencode")
        sf = self.tmp / ".agent-toggle/state.json"
        good = json.loads(sf.read_text(encoding="utf-8"))
        key = "opencode:mcp:example-mcp"
        flag_file = good["disabled"][key]["flag"]["file"]
        cases = {
            "connector, no flag": lambda e: (e.update(connector=True), e.pop("flag")),
            "move + string flag": lambda e: e.update(mechanism="move", flag=flag_file),
            "native_cli + bad was": lambda e: e.update(mechanism="native_cli",
                                                       flag=dict(e["flag"], was="no")),
            "flag is a list": lambda e: e.update(flag=[flag_file]),
        }
        for label, mutate in cases.items():
            with self.subTest(label):
                st = json.loads(json.dumps(good))
                mutate(st["disabled"][key])
                sf.write_text(json.dumps(st), encoding="utf-8")
                rc, out, _ = self.run_cli("enable", "mcp", "example-mcp", "--harness", "opencode")
                self.assertEqual(rc, 1)
                self.assertIn("refused", out)
                self.assertNotIn("KeyError", out + _)
                self.assertEqual(self.raw("opencode"), disabled)

    def test_flag_disable_never_overwrites_another_mechanisms_entry(self) -> None:
        d = self.tmp / ".openclaw/skills/p2"
        d.mkdir()
        (d / "SKILL.md").write_text("x", encoding="utf-8")
        self.run_cli("disable", "skill", "p2", "--harness", "openclaw")      # dir move, no entry yet
        f = self.tmp / self.FILES["openclaw"]
        f.write_bytes(f.read_bytes().replace(b'"demo-skill" :{', b'"p2": {"enabled": true}, "demo-skill" :{'))
        before = f.read_bytes()
        rc, out, _ = self.run_cli("disable", "skill", "p2", "--harness", "openclaw")
        self.assertEqual(rc, 1)
        self.assertIn("already disabled", out)
        self.assertEqual(f.read_bytes(), before)
        self.assertEqual(self.state()["disabled"]["openclaw:skill:p2"]["mechanism"], "move")

    def test_unsafe_config_falls_back_to_dir_move_with_a_warning(self) -> None:
        f = self.tmp / self.FILES["openclaw"]
        f.write_text('{"skills": {"entries": {"demo-skill": {"enabled": true, "enabled": false}}}}',
                     encoding="utf-8")
        before = f.read_bytes()
        rc, out, err = self.run_cli("disable", "skill", "demo-skill", "--harness", "openclaw")
        self.assertEqual(rc, 0)
        self.assertIn("duplicate key", out + err)
        self.assertTrue((self.tmp / ".openclaw/skills-disabled/demo-skill/SKILL.md").is_file())
        self.assertEqual(f.read_bytes(), before)

    def test_flag_file_not_editable_is_refused(self) -> None:
        from dataclasses import replace

        from agent_toggle import mechanisms
        table = build(self.tmp)
        table["openclaw"] = replace(table["openclaw"], editable=frozenset())
        orig = mechanisms.harnesses
        mechanisms.harnesses = lambda: table
        self.addCleanup(setattr, mechanisms, "harnesses", orig)
        before = self.raw("openclaw")
        rc, out, _ = self.run_cli("disable", "plugin", "demo-plugin", "--harness", "openclaw")
        self.assertEqual(rc, 1)
        self.assertEqual(self.raw("openclaw"), before)

    def test_unclaimed_flag_pairs_still_exit_4(self) -> None:
        for h, t in (("openclaw", "mcp"), ("opencode", "plugin"), ("opencode", "agent")):
            self.assertEqual(self.run_cli("disable", t, "x", "--harness", h)[0], 4)
