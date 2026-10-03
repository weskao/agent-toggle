"""tools/gen_completions.py builds all three shells from the live parser."""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from agent_toggle import cli
from agent_toggle.harnesses import TYPES

GEN = Path(__file__).resolve().parent.parent / "tools" / "gen_completions.py"
spec = importlib.util.spec_from_file_location("gen_completions", GEN)
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)


class CompletionsTest(unittest.TestCase):
    def test_every_command_in_every_shell(self):
        files = gen.generate()
        self.assertEqual(len(files), 3)
        for name, text in files.items():
            for cmd in cli.COMMANDS:
                self.assertRegex(text, rf"\b{cmd}\b", f"{cmd} missing from {name}")
            self.assertIn("--harness", text)
            for t in TYPES:
                self.assertIn(t, text)

    def test_main_writes_files(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(gen.main([d]), 0)
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()),
                             ["_agent-toggle", "agent-toggle.bash", "agent-toggle.fish"])


if __name__ == "__main__":
    unittest.main()
