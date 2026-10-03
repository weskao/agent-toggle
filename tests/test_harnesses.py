"""The harness table: frozen records, today's capabilities, sandbox HOME."""
from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from base import SandboxCase

from agent_toggle import harnesses as hz
from agent_toggle.harnesses import build

TODAY = {
    ("claude", t) for t in ("skill", "agent", "command", "rule", "plugin", "mcp")
} | {
    ("codex", t) for t in ("skill", "agent", "command", "plugin", "mcp")
} | {("opencode", "skill"), ("opencode", "command"),
     ("grok", "skill"), ("grok", "mcp"), ("openclaw", "skill"), ("openclaw", "agent"),
     ("openclaw", "plugin"), ("opencode", "mcp"), ("vibe", "skill"),
     ("copilot", "skill"), ("copilot", "agent"), ("copilot", "mcp")}


class HarnessTableTest(SandboxCase):
    def test_records_are_frozen(self) -> None:
        h = build(self.tmp)["claude"]
        with self.assertRaises(dataclasses.FrozenInstanceError):
            h.name = "x"                       # type: ignore[misc]
        with self.assertRaises(TypeError):
            h.dirs["skill"] = ("elsewhere",)   # type: ignore[index]
        with self.assertRaises(TypeError):
            h.mechanisms["skill"] = "flag"     # type: ignore[index]

    def test_supported_pairs_match_today(self) -> None:
        pairs = {(n, t) for n, h in build(self.tmp).items() for t in h.types}
        self.assertEqual(pairs, TODAY)

    def test_docs_mirror_the_verified_versions(self) -> None:
        doc = (Path(__file__).resolve().parent.parent / "docs" / "harnesses.md").read_text(encoding="utf-8")
        for n, h in build(self.tmp).items():
            self.assertRegex(doc, rf"(?m)^\| {n} \| {re.escape(h.verified)} \|", n)

    def test_every_move_pair_has_dirs(self) -> None:
        for n, h in build(self.tmp).items():
            for t, mech in h.mechanisms.items():
                if mech == "move":
                    self.assertTrue(h.dirs.get(t), f"{n}:{t} claims move but has no dirs")

    def test_rule_is_claude_only_and_codex_probes_prompts(self) -> None:
        t = build(self.tmp)
        self.assertEqual([n for n, h in t.items() if "rule" in h.types], ["claude"])
        self.assertEqual(t["claude"].dirs["rule"], ("rules",))
        self.assertEqual(t["codex"].dirs["command"], ("commands", "prompts"))

    def test_homes_and_mcp_backends(self) -> None:
        t = build(self.tmp)
        self.assertEqual(t["claude"].home, self.tmp / ".claude")
        self.assertEqual(t["claude"].backend, "claude-json")
        self.assertEqual(t["codex"].backend, "toml")
        self.assertEqual(t["grok"].mcp.file, self.tmp / ".grok" / "config.toml")
        self.assertIsNone(t["openclaw"].mcp)
        self.assertEqual(t["copilot"].home, self.tmp / ".copilot")
        self.assertEqual(t["copilot"].backend, "json")
        self.assertEqual(t["copilot"].mcp.file, self.tmp / ".copilot" / "mcp-config.json")
        self.assertEqual(t["copilot"].editable, {"mcp-config.json"})

    def test_flag_pairs_are_declared_and_editable(self) -> None:
        for n, h in build(self.tmp).items():
            for t, (rel, pointer) in h.flags.items():
                self.assertIn(rel, h.editable, f"{n}:{t} flag file is not editable")
                self.assertIn("<name>", pointer)
            for t, mech in h.mechanisms.items():
                if mech == "flag":
                    self.assertIn(t, h.flags, f"{n}:{t} claims flag but declares none")
        t = build(self.tmp)
        self.assertEqual(t["opencode"].mechanisms["mcp"], "flag")
        self.assertEqual(t["openclaw"].mechanisms["plugin"], "flag")
        self.assertEqual(t["openclaw"].mechanisms["skill"], "move")   # flag only when an entry exists
        self.assertEqual(t["opencode"].flags["mcp"], ("opencode.json", ("mcp", "<name>", "enabled")))

    def test_shim_lists_every_type_and_harness(self) -> None:
        for tmpl in ("claude.md.tmpl", "generic.md.tmpl"):
            shim = (Path(hz.__file__).parent / "shims" / tmpl).read_text(encoding="utf-8")
            for t in hz.TYPES:
                self.assertIn(f"`{t}`", shim, tmpl)
            self.assertIn("--harness " + "|".join(build(self.tmp)), shim, tmpl)

    def test_harnesses_honours_sandbox_home(self) -> None:
        self.assertEqual(hz.harnesses()["codex"].home, self.tmp / ".codex")


class MultiCandidateDirsTest(SandboxCase):
    """dirs[type] may list several subdirs (e.g. commands + prompts); each is probed."""

    def test_disable_and_enable_use_the_subdir_that_holds_the_item(self) -> None:
        from agent_toggle import mechanisms
        from agent_toggle.harnesses import Harness
        two = Harness("claude", self.home,
                      dirs={"command": ("commands", "prompts")},
                      mechanisms={"command": "move"})
        self.write("prompts/hello.md")
        state = {"version": 3, "disabled": {}}
        orig = mechanisms.harnesses
        mechanisms.harnesses = lambda: {"claude": two}
        self.addCleanup(setattr, mechanisms, "harnesses", orig)
        self.assertEqual(
            mechanisms.toggle_dir_type("disable", "command", ["hello"], state, "claude", self.home), 0)
        self.assertTrue((self.home / "prompts-disabled" / "hello.md").is_file())
        self.assertEqual(
            mechanisms.toggle_dir_type("enable", "command", ["hello"], state, "claude", self.home), 0)
        self.assertTrue((self.home / "prompts" / "hello.md").is_file())
