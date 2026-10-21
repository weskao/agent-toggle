"""The pane mod ships with the CLI: one version, so `claude plugin update` fetches the mod on every release."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from agent_toggle import __version__

PLUGIN_JSON = Path(__file__).resolve().parent.parent / "mod" / ".claude-plugin" / "plugin.json"


class ModVersionTest(unittest.TestCase):
    def test_the_mod_version_is_the_cli_version(self) -> None:
        mod = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))["version"]
        self.assertEqual(mod, __version__, "bump mod/.claude-plugin/plugin.json with agent_toggle/__init__.py")


if __name__ == "__main__":
    unittest.main()
