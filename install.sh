#!/usr/bin/env bash
# Install the agent-toggle skill shim into every harness found on this machine.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
exec python3 "$ROOT/agent_toggle.py" install-shims "$@"
