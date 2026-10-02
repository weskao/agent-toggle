#!/usr/bin/env python3
"""Run agent-toggle from a checkout: `python3 agent_toggle.py <command>`.

The implementation lives in the agent_toggle/ package next to this file.
"""
import sys

from agent_toggle.cli import main

raise SystemExit(main(sys.argv[1:]))
