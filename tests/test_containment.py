"""Names are validated at the CLI boundary and items must sit inside their harness dir."""
from __future__ import annotations

import unittest

from base import CAN_SYMLINK, SandboxCase

from agent_toggle import cli


class NameValidationTest(SandboxCase):
    def setUp(self) -> None:
        super().setUp()
        # a victim directory outside the harness, reachable via ../
        self.victim = self.tmp / "victim"
        (self.victim / "SKILL.md").parent.mkdir()
        (self.victim / "SKILL.md").write_text("not ours\n", encoding="utf-8")

    def assert_refused(self, *argv: str) -> None:
        self.assertEqual(cli.main(list(argv)), 2, argv)
        self.assertTrue((self.victim / "SKILL.md").is_file(), "victim was moved")
        self.assertFalse((self.home / "skills-disabled").exists(), "something was parked")

    def test_dotdot_name_is_refused(self) -> None:
        self.assert_refused("disable", "skill", "../../victim")

    def test_dotdot_via_colon_nesting_is_refused(self) -> None:
        self.assert_refused("disable", "skill", "..:..:victim")

    def test_absolute_name_is_refused(self) -> None:
        self.assert_refused("disable", "skill", str(self.victim))

    def test_empty_part_is_refused(self) -> None:
        self.assert_refused("disable", "skill", "a::b")

    def test_leading_dash_part_is_refused(self) -> None:
        self.assert_refused("disable", "skill", "a:-b")

    def test_enable_validates_too(self) -> None:
        self.assert_refused("enable", "skill", "../../victim")

    def test_dry_run_validates_too(self) -> None:
        self.assert_refused("disable", "skill", "../../victim", "--dry-run")

    def test_refusal_names_the_bad_name_in_json(self) -> None:
        import contextlib
        import io
        import json
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.main(["disable", "skill", "../../victim", "--json"])
        self.assertEqual(rc, 2)
        env = json.loads(buf.getvalue())
        self.assertFalse(env["ok"])
        self.assertIn("../../victim", json.dumps(env))

    def test_ordinary_and_nested_names_still_work(self) -> None:
        (self.home / "skills" / "demo-skill").mkdir()
        (self.home / "commands" / "demo").mkdir()
        (self.home / "commands" / "demo" / "batch.md").write_text("x\n", encoding="utf-8")
        self.assertEqual(cli.main(["disable", "skill", "demo-skill"]), 0)
        self.assertEqual(cli.main(["disable", "command", "demo:batch"]), 0)


@unittest.skipUnless(CAN_SYMLINK, "cannot create symlinks here")
class SymlinkItemTest(SandboxCase):
    def test_symlinked_item_is_moved_as_a_link_not_followed(self) -> None:
        target = self.tmp / "elsewhere" / "linked-skill"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("x\n", encoding="utf-8")
        (self.home / "skills" / "linked-skill").symlink_to(target)
        self.assertEqual(cli.main(["disable", "skill", "linked-skill"]), 0)
        parked = self.home / "skills-disabled" / "linked-skill"
        self.assertTrue(parked.is_symlink())
        self.assertTrue((target / "SKILL.md").is_file(), "link target was moved")
        self.assertEqual(cli.main(["enable", "skill", "linked-skill"]), 0)
        self.assertTrue((self.home / "skills" / "linked-skill").is_symlink())

    def test_nested_symlink_dir_cannot_reach_outside(self) -> None:
        outside = self.tmp / "outside"
        (outside / "victim").mkdir(parents=True)
        (self.home / "commands" / "group").symlink_to(outside)
        self.assertNotEqual(cli.main(["disable", "command", "group:victim"]), 0)
        self.assertTrue((outside / "victim").is_dir(), "item outside the harness was moved")
