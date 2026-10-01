"""safe_move: the guard against `mv X dest/` renaming X to dest."""
from __future__ import annotations

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
        self.assertEqual((dest / "demo" / "SKILL.md").read_text(), "hello")
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
        self.assertEqual((self.home / "skills-disabled/a/SKILL.md").read_text(), "old")
