#!/bin/sh
# Install the agent-toggle shim into every harness found on this machine.
# The tool itself stays here; each harness gets only a pointer to it.
set -eu

ROOT=$(cd "$(dirname "$0")" && pwd)
SHIM="$ROOT/shims/claude/SKILL.md"

# Harness dirs are often git repos that sync across machines, so write the
# path `~`-relative whenever this repo lives under $HOME.
case "$ROOT" in
    "$HOME"/*) SHIM_ROOT="~${ROOT#"$HOME"}" ;;
    *)         SHIM_ROOT="$ROOT" ;;
esac

[ -f "$SHIM" ] || { echo "missing shim: $SHIM" >&2; exit 1; }

installed=0
for home in "$HOME/.claude" "$HOME/.codex" "$HOME/.grok" "$HOME/.openclaw"; do
    [ -d "$home" ] || continue
    dest="$home/skills/agent-toggle"
    mkdir -p "$dest"
    # Bake in where this repo actually sits, so a clone anywhere works.
    sed "s|__AGENT_TOGGLE_ROOT__|$SHIM_ROOT|g" "$SHIM" > "$dest/SKILL.md"
    echo "  installed  $dest/SKILL.md"
    installed=$((installed + 1))

    # A park dir that git tracks turns every disable into dozens of deletion
    # lines in `git status`. Only touch a .gitignore that already exists.
    ignore="$home/.gitignore"
    if [ -f "$ignore" ]; then
        for d in skills-disabled agents-disabled commands-disabled; do
            grep -qx "$d/" "$ignore" || echo "$d/" >> "$ignore"
        done
    fi
done

[ "$installed" -gt 0 ] || { echo "no harness found" >&2; exit 1; }
echo "$installed harness(es) installed"
echo "next: python3 $ROOT/agent_toggle.py status"
