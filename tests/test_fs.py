"""safe_move: the guard against `mv X dest/` renaming X to dest."""
from __future__ import annotations

import os
import unittest
from unittest import mock

from base import SandboxCase

from agent_toggle import fs


class SafeMoveTest(SandboxCase):
    def test_never_renames_source_into_dest(self) -> None:
        """The bug this guard exists for: `mv X dest/` renames X to dest."""
        src = self.write("skills/demo/SKILL.md", "hello")
        dest = self.home / "skills-disabled"
        self.assertFalse(dest.exists())
        fs.safe_move(src.parent, dest)
        # safe_move keeps the item as a CHILD of dest
        self.assertEqual((dest / "demo" / "SKILL.md").read_text(encoding="utf-8"), "hello")
        # safe_move did not rename src to dest
        self.assertTrue(dest.is_dir())

    def test_refuses_file_as_dest_dir(self) -> None:
        src = self.write("skills/a/SKILL.md")
        blocker = self.write("skills-disabled", "i am a file")
        with self.assertRaises(NotADirectoryError):
            fs.safe_move(src.parent, blocker)

    def test_refuses_overwrite(self) -> None:
        src = self.write("skills/a/SKILL.md", "new")
        self.write("skills-disabled/a/SKILL.md", "old")
        with self.assertRaises(FileExistsError):
            fs.safe_move(src.parent, self.home / "skills-disabled")
        # existing parked copy untouched
        self.assertEqual((self.home / "skills-disabled/a/SKILL.md").read_text(encoding="utf-8"), "old")


POSIX = os.name != "nt"


class StateSubdirsTest(SandboxCase):
    def test_profiles_and_parked_live_under_state_dir(self) -> None:
        self.assertEqual(fs.profiles_dir(), fs.state_dir() / "profiles")
        self.assertEqual(fs.parked_dir(), fs.state_dir() / "parked")


class ContainedTest(SandboxCase):
    def test_item_inside_root_is_contained(self) -> None:
        item = self.write("skills/demo-skill/SKILL.md").parent
        self.assertTrue(fs.contained(item, self.home / "skills"))
        # nested item, second root
        self.assertTrue(fs.contained(item / "SKILL.md", self.home / "agents", self.home / "skills"))

    def test_outside_every_root_is_refused(self) -> None:
        self.assertFalse(fs.contained(self.tmp / "elsewhere" / "x", self.home / "skills"))

    def test_dotdot_is_refused_even_when_it_lands_inside(self) -> None:
        self.write("skills/demo-skill/SKILL.md")
        self.assertFalse(fs.contained(self.home / "skills" / "a" / ".." / "demo-skill",
                                      self.home / "skills"))
        self.assertFalse(fs.contained(self.home / "skills" / ".." / ".." / "x",
                                      self.home / "skills"))

    @unittest.skipIf(not POSIX, "symlinks")
    def test_symlinked_parent_escaping_the_root_is_refused(self) -> None:
        outside = self.tmp / "outside"
        (outside / "demo-skill").mkdir(parents=True)
        (self.home / "skills" / "link").symlink_to(outside)
        self.assertFalse(fs.contained(self.home / "skills" / "link" / "demo-skill",
                                      self.home / "skills"))

    @unittest.skipIf(not POSIX, "symlinks")
    def test_item_symlink_is_not_followed(self) -> None:
        outside = self.tmp / "outside"
        outside.mkdir()
        (self.home / "skills" / "demo-skill").symlink_to(outside)
        self.assertTrue(fs.contained(self.home / "skills" / "demo-skill", self.home / "skills"))

    @unittest.skipIf(not POSIX, "symlinks")
    def test_parked_dir_reached_through_a_symlink_is_refused(self) -> None:
        real = self.tmp / "real-state"
        (real / "parked").mkdir(parents=True)
        fs.state_dir().symlink_to(real)
        self.assertFalse(fs.contained(fs.parked_dir() / "demo-skill", fs.parked_dir()))
        # the parked dir itself as a symlink too
        fs.state_dir().unlink()
        fs.state_dir().mkdir()
        fs.parked_dir().symlink_to(real / "parked")
        self.assertFalse(fs.contained(fs.parked_dir() / "demo-skill", fs.parked_dir()))

    def test_parked_dir_ok(self) -> None:
        fs.parked_dir().mkdir(parents=True)
        self.assertTrue(fs.contained(fs.parked_dir() / "claude" / "demo-skill", fs.parked_dir()))


class CheckedWriteTest(SandboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.cfg = self.write("config.json", '{"a": 1}\n')
        if POSIX:
            self.cfg.chmod(0o640)

    def test_ok_write_lands_and_keeps_mode(self) -> None:
        note = fs.checked_write(self.cfg, '{"a": 2}\n', fs.json_verify())
        self.assertEqual(note, "")
        self.assertEqual(self.cfg.read_bytes(), b'{"a": 2}\n')
        if POSIX:
            self.assertEqual(self.cfg.stat().st_mode & 0o777, 0o640)

    def test_failed_verify_restores_bytes(self) -> None:
        with self.assertRaises(fs.WriteError) as cm:
            fs.checked_write(self.cfg, "{not json", fs.json_verify())
        self.assertIn(str(self.cfg), str(cm.exception))
        self.assertEqual(self.cfg.read_bytes(), b'{"a": 1}\n')
        self.assertEqual(list(self.home.glob(".config.json.*")), [])   # no tmp left

    @unittest.skipIf(not POSIX, "POSIX modes")
    def test_failed_verify_restores_mode(self) -> None:
        def chmod_then_fail(before: str, after: str) -> str:
            self.cfg.chmod(0o600)          # something changed the mode mid-write
            raise ValueError("boom")
        with self.assertRaises(fs.WriteError):
            fs.checked_write(self.cfg, '{"a": 3}\n', chmod_then_fail)
        self.assertEqual(self.cfg.read_bytes(), b'{"a": 1}\n')
        self.assertEqual(self.cfg.stat().st_mode & 0o777, 0o640)

    def test_failed_write_of_a_new_file_leaves_nothing(self) -> None:
        new = self.home / "new.json"
        with self.assertRaises(fs.WriteError):
            fs.checked_write(new, "{not json", fs.json_verify())
        self.assertFalse(new.exists())

    @unittest.skipIf(not POSIX, "symlinks")
    def test_symlinked_config_is_written_through(self) -> None:
        link = self.home / "linked.json"
        link.symlink_to(self.cfg)
        fs.checked_write(link, '{"a": 2}\n', fs.json_verify())
        self.assertTrue(link.is_symlink())
        self.assertEqual(self.cfg.read_bytes(), b'{"a": 2}\n')
        with self.assertRaises(fs.WriteError):
            fs.checked_write(link, "{bad", fs.json_verify())
        self.assertTrue(link.is_symlink())
        self.assertEqual(self.cfg.read_bytes(), b'{"a": 2}\n')

    def test_interrupt_mid_verify_still_rolls_back(self) -> None:
        def interrupted(before: str, after: str) -> str:
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            fs.checked_write(self.cfg, '{"a": 9}\n', interrupted)
        self.assertEqual(self.cfg.read_bytes(), b'{"a": 1}\n')

    def test_a_failed_first_write_is_not_reported_as_a_failed_restore(self) -> None:
        with mock.patch.object(fs, "_replace_bytes", side_effect=PermissionError("read-only dir")):
            with self.assertRaises(fs.WriteError) as cm:
                fs.checked_write(self.cfg, '{"a": 2}\n', fs.json_verify())
        self.assertNotIn("restore failed", str(cm.exception))
        self.assertIn("rolled back", str(cm.exception))
        self.assertEqual(self.cfg.read_bytes(), b'{"a": 1}\n')

    def test_verify_sees_before_and_after(self) -> None:
        seen = []
        fs.checked_write(self.cfg, "{}", lambda b, a: seen.append((b, a)) or "")
        self.assertEqual(seen, [('{"a": 1}\n', "{}")])


class VerifyTest(unittest.TestCase):
    BEFORE = '[mcp_servers.example-mcp]\ncommand = "demo"\n\n[other]\nx = 1\n'
    AFTER = '[other]\nx = 1\n'

    def test_json_verify(self) -> None:
        self.assertEqual(fs.json_verify()("{}", '{"a": 1}'), "")
        with self.assertRaises(ValueError):
            fs.json_verify()("{}", "{nope")

    def test_json_verify_expected_is_byte_for_byte(self) -> None:
        self.assertEqual(fs.json_verify('{"a": true}')("{}", '{"a": true}'), "")
        with self.assertRaises(ValueError):
            fs.json_verify('{"a": true}')("{}", '{"a":true}')

    @unittest.skipIf(fs._tomllib() is None, "needs tomllib (3.11+)")
    def test_toml_verify_parses_with_tomllib(self) -> None:
        self.assertEqual(fs.toml_verify()(self.BEFORE, self.AFTER), "")
        with self.assertRaises(ValueError):
            fs.toml_verify()(self.BEFORE, "[broken\n")
        with self.assertRaises(ValueError):
            fs.toml_verify(self.AFTER)(self.BEFORE, self.AFTER + "y = 2\n")

    def test_toml_verify_without_tomllib_is_textual(self) -> None:
        with mock.patch.object(fs, "_tomllib", lambda: None):
            self.assertEqual(fs.toml_verify(self.AFTER)(self.BEFORE, self.AFTER), "")
            with self.assertRaises(ValueError):
                fs.toml_verify(self.AFTER)(self.BEFORE, self.AFTER + "y = 2\n")
            self.assertEqual(fs.toml_verify()(self.BEFORE, self.AFTER),
                             "unverified (no tomllib)")
