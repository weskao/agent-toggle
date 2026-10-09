"""Install the built wheel the way a PyPI user gets it, and run it against a throwaway HOME.

`python tools/wheel_smoke.py` from the repo root: builds the wheel, installs it with no
extras into a fresh venv outside the checkout, then runs the CLI. Exits non-zero on failure.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="agent-toggle-smoke-"))
dist = root / "dist"
subprocess.check_call([sys.executable, "-m", "pip", "wheel", "--no-deps", "-q", "-w", str(dist), "."])
venv.create(root / "venv", with_pip=True)
bindir = root / "venv" / ("Scripts" if os.name == "nt" else "bin")
subprocess.check_call([str(bindir / "python"), "-m", "pip", "install", "-q",
                       glob.glob(str(dist / "*.whl"))[0]])
exe = shutil.which("agent-toggle", path=str(bindir))
assert exe, f"no agent-toggle console script in {bindir}"

home = root / "home"
skill = home / ".claude" / "skills" / "demo"
skill.mkdir(parents=True)
(skill / "SKILL.md").write_text("---\nname: demo\ndescription: smoke\n---\n", encoding="utf-8")
env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
env.pop("XDG_CONFIG_HOME", None)

for args in (["version"], ["status", "--json"], ["list"], ["doctor"], ["install-shims"],
             ["disable", "skill", "demo"], ["enable", "skill", "demo"],
             ["disable", "skill", "demo"], ["undo"]):
    print("$ agent-toggle", *args, flush=True)
    subprocess.run([exe, *args], env=env, cwd=root, stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, check=True)

assert (skill / "SKILL.md").is_file(), "demo skill was not restored"
assert (home / ".claude" / "skills" / "agent-toggle" / "SKILL.md").is_file(), "no shim written"
assert (home / ".agent-toggle" / "state.json").is_file(), "state not under HOME"
print("wheel smoke: ok")
