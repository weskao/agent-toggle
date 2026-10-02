"""Park / restore of dir-type resources, and the harness/backend gates."""
from __future__ import annotations

import os
import unittest
from pathlib import Path

from base import SandboxCase

from agent_toggle import mechanisms as mech


class DirTypeTest(SandboxCase):
    def test_nested_command_roundtrip(self) -> None:
        """`orch:batch` lives at commands/orch/batch.md and must come back there."""
        self.write("commands/orch/batch.md", "batch")
        self.write("commands/other/batch.md", "other")
        state = {"version": 2, "disabled": {}}

        mech.toggle_dir_type("disable", "command", ["orch:batch"], state, "claude", self.home)
        # nested command parked with its parent dir
        self.assertTrue((self.home / "commands-disabled/orch/batch.md").is_file())
        # sibling with same basename untouched
        self.assertEqual((self.home / "commands/other/batch.md").read_text(encoding="utf-8"), "other")
        # origin recorded
        origin = state["disabled"]["claude:command:orch:batch"]["origin"]
        self.assertEqual(Path(origin).parts[-3:], ("commands", "orch", "batch.md"))

        mech.toggle_dir_type("enable", "command", ["orch:batch"], state, "claude", self.home)
        # nested command restored to its original path
        self.assertEqual((self.home / "commands/orch/batch.md").read_text(encoding="utf-8"), "batch")
        # state entry cleared
        self.assertFalse(state["disabled"])
        # empty park subdir pruned
        self.assertFalse((self.home / "commands-disabled/orch").exists())
        # park root kept
        self.assertTrue((self.home / "commands-disabled").is_dir())

    def test_resolve_probes_shapes(self) -> None:
        self.write("skills/dir-skill/SKILL.md")
        self.write("agents/file-agent.md")
        # resolves a directory skill
        self.assertTrue(mech.resolve_item(self.home / "skills", "dir-skill").is_dir())
        # resolves an .md agent without naming the suffix
        self.assertEqual(mech.resolve_item(self.home / "agents", "file-agent").suffix, ".md")
        # missing item resolves to None
        self.assertIsNone(mech.resolve_item(self.home / "agents", "nope"))

    @unittest.skipIf(os.name == "nt", "symlinks need privilege / Developer Mode on Windows")
    def test_symlink_disable_roundtrip(self) -> None:
        live = self.home / "skills"
        link = live / "broken-symlink"
        link.symlink_to(self.home / "nonexistent-target")
        state = {"version": 2, "disabled": {}}
        fails = mech.toggle_dir_type("disable", "skill", ["broken-symlink"], state,
                                     "claude", self.home)
        # broken symlink disabled without error
        self.assertEqual(fails, 0)
        # broken symlink moved to parked
        self.assertTrue((self.home / "skills-disabled" / "broken-symlink").is_symlink())
        # broken symlink gone from live
        self.assertFalse(link.exists() or link.is_symlink())
        fails_enable = mech.toggle_dir_type("enable", "skill", ["broken-symlink"], state,
                                            "claude", self.home)
        # broken symlink restored without error
        self.assertEqual(fails_enable, 0)
        # broken symlink back in live
        self.assertTrue(link.is_symlink())


class McpGateTest(SandboxCase):
    def test_mcp_without_backend_fails_loudly(self) -> None:
        state = {"version": 2, "disabled": {}}
        fails = mech.toggle_mcp("disable", ["x"], state, "openclaw", self.home, None)
        # sqlite-backed harness reports failure instead of no-op
        self.assertEqual(fails, 1)
        # no state written for the refused server
        self.assertFalse(state["disabled"])
