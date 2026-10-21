---
paths:
  - agent_toggle/fs.py
  - agent_toggle/mechanisms.py
  - agent_toggle/companions.py
  - agent_toggle/backends/**
  - agent_toggle/harnesses.py
  - docs/toggle-paths.md
  - docs/harnesses.md
---
# Keep `docs/toggle-paths.md` in sync

It documents what `disable` / `enable` change on disk per type, with example live, parked and
backup paths. Update it in the same commit as a change to:

- park / companion / backup locations (`user_park`, `parked_dir`, `companion_dir`, `backup_dir`
  in `fs.py`; backup file names in `mechanisms.py`: `<harness>__<name>.json`,
  `<sha8>__<harness>__<name>.json`);
- how a type is toggled (move / native CLI / flag / remove-with-backup in `mechanisms.py`,
  `companions.py`, `backends/`);
- a harness row's `dirs`, `mechanisms`, `flags` or `mcp` in `harnesses.py` (also update
  `docs/harnesses.md`);
- the state key (`<harness>:<type>:<name>`) or project park layout
  (`parked/<sha8>/<sub>-disabled`).

Examples stay synthetic (`demo-skill`, `example-mcp`). After changing a move path, verify the
doc's example paths with a real disable/enable round trip in a temp `HOME`.
