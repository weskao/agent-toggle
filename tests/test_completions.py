"""agent_toggle.completions builds every shell's script from the live parser."""
from __future__ import annotations

import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_toggle import cli, completions
from agent_toggle.harnesses import TYPES

GEN = Path(__file__).resolve().parent.parent / "tools" / "gen_completions.py"
spec = importlib.util.spec_from_file_location("gen_completions", GEN)
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)

# shell -> argv that parses the script at {} without running it
CHECK = {"bash": ["bash", "-n", "{}"], "zsh": ["zsh", "-n", "{}"],
         "fish": ["fish", "--no-execute", "{}"],
         # -Command folds trailing args into the command text, so the path goes inside it
         "powershell": ["pwsh", "-NoProfile", "-NonInteractive", "-Command",
                        "$e = $null; [void][System.Management.Automation.Language.Parser]"
                        "::ParseFile('{}', [ref]$null, [ref]$e); if ($e) { $e; exit 1 }"]}


class CompletionsTest(unittest.TestCase):
    def test_every_command_in_every_shell(self):
        parser = cli.build_parser()
        self.assertEqual(set(completions.SHELLS), set(CHECK))
        for shell in completions.SHELLS:
            text = completions.script(shell, parser)
            for cmd in cli.COMMANDS:
                self.assertRegex(text, rf"\b{cmd}\b", f"{cmd} missing from {shell}")
            self.assertIn("--harness", text)
            for t in TYPES:
                self.assertIn(t, text)

    def test_every_script_parses_in_its_shell(self):
        parser = cli.build_parser()
        checked = 0
        for shell, argv in CHECK.items():
            exe = shutil.which(argv[0])
            if not exe:
                continue
            with tempfile.TemporaryDirectory() as d:
                path = Path(d) / f"agent-toggle.{shell}"
                path.write_text(completions.script(shell, parser), encoding="utf-8")
                done = subprocess.run([exe, *(a.replace("{}", str(path)) for a in argv[1:])],
                                      capture_output=True,
                                      text=True, encoding="utf-8", check=False)
            self.assertEqual(done.returncode, 0, f"{shell}: {done.stdout}{done.stderr}")
            checked += 1
        if not checked:
            self.skipTest("no supported shell on PATH")

    @unittest.skipIf(os.name == "nt" or not shutil.which("bash"), "needs a POSIX bash")
    def test_bash_completes_commands_and_flag_values(self):
        script = completions.script("bash", cli.build_parser())

        def complete(*words: str) -> set[str]:
            line = " ".join(f"'{w}'" for w in words)
            probe = (f"{script}\nCOMP_WORDS=({line}); COMP_CWORD={len(words) - 1}\n"
                     '_agent_toggle; printf "%s\\n" "${COMPREPLY[@]}"')
            return set(subprocess.run(["bash", "-c", probe], capture_output=True, text=True,
                                      encoding="utf-8", check=True).stdout.split())

        self.assertIn("completion", complete("agent-toggle", "comp"))
        shells = complete("agent-toggle", "completion", "")     # plus the global flags
        self.assertEqual({w for w in shells if not w.startswith("-")}, set(completions.SHELLS))
        self.assertEqual(complete("agent-toggle", "--harness", "cl"), {"claude"})

    def test_command_writes_lf_even_where_stdout_translates_newlines(self):
        # Windows text stdout turns \n into \r\n, and bash rejects a script with \r in it
        raw = io.BytesIO()
        out = io.TextIOWrapper(raw, encoding="utf-8", newline="\r\n")
        with mock.patch.object(sys, "stdout", out):
            self.assertEqual(cli.main(["completion", "bash"]), 0)
            out.flush()
        self.assertNotIn(b"\r", raw.getvalue())
        self.assertEqual(raw.getvalue().decode("utf-8"),
                         completions.script("bash", cli.build_parser()))

    def test_tool_writes_one_file_per_shell(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(gen.main([d]), 0)
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()),
                             ["_agent-toggle", "agent-toggle.bash", "agent-toggle.fish",
                              "agent-toggle.ps1"])


if __name__ == "__main__":
    unittest.main()
