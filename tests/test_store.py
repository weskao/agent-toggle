"""Legacy ~/.claude-toggle import."""
from __future__ import annotations

import json
from pathlib import Path

from base import SandboxCase

from agent_toggle import fs, store


class MigrateTest(SandboxCase):
    def test_migrate_imports_legacy_state(self) -> None:
        legacy = fs.legacy_state_dir()
        (legacy / "mcp-backups").mkdir(parents=True)
        backup = legacy / "mcp-backups/example-mcp.json"
        backup.write_text('{"type": "http", "url": "http://127.0.0.1:8765/mcp"}')
        (legacy / "state.json").write_text(json.dumps({"version": 1, "disabled": {
            "mcp:example-mcp": {"type": "mcp", "name": "example-mcp",
                                "backup": str(backup), "at": "2026-09-19T00:00:00+0800"},
        }}))
        state = {"version": 2, "disabled": {}}
        store.migrate(state)

        entry = state["disabled"].get("claude:mcp:example-mcp")
        # legacy entry imported under a harness key
        self.assertIsNotNone(entry)
        # imported entry is tagged claude
        self.assertEqual(entry["harness"], "claude")
        # backup copied into the new store
        self.assertTrue(Path(entry["backup"]).is_file())
        # legacy backup left in place
        self.assertTrue(backup.is_file())
