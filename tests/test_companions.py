"""Companion scan: exclusive helpers travel with the item, shared ones stay."""
from __future__ import annotations

from pathlib import Path

from base import SandboxCase

from agent_toggle import companions, mechanisms


class CompanionTest(SandboxCase):
    def test_exclusive_companion_moves_shared_one_stays(self) -> None:
        self.write("scripts/only-mine.sh", "#!/bin/sh")
        self.write("scripts/shared.sh", "#!/bin/sh")
        self.write("skills/solo/SKILL.md",
                   "run $HOME/.claude/scripts/only-mine.sh and scripts/shared.sh")
        self.write("skills/other/SKILL.md", "I also call scripts/shared.sh")
        state = {"version": 2, "disabled": {}}

        mechanisms.toggle_dir_type("disable", "skill", ["solo"], state, "claude", self.home)
        entry = state["disabled"]["claude:skill:solo"]
        moved = [Path(c["from"]).name for c in entry["companions"]]

        # exclusive companion parked
        self.assertIn("only-mine.sh", moved)
        # exclusive companion gone from live tree
        self.assertFalse((self.home / "scripts/only-mine.sh").exists())
        # shared companion NOT parked
        self.assertNotIn("shared.sh", moved)
        # shared companion still live
        self.assertTrue((self.home / "scripts/shared.sh").is_file())

        mechanisms.toggle_dir_type("enable", "skill", ["solo"], state, "claude", self.home)
        # companion restored on enable
        self.assertTrue((self.home / "scripts/only-mine.sh").is_file())

    def test_companion_scan_ignores_item_internals(self) -> None:
        self.write("skills/kit/SKILL.md", "see helper.py in this folder")
        self.write("skills/kit/helper.py", "print(1)")
        comps = companions.find_companions(self.home / "skills/kit", self.home)
        # files inside the item are not companions
        self.assertEqual(comps, [])
