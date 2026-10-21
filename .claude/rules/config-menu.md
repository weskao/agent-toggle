---
paths:
  - agent_toggle/ui/config_menu.py
---
Telegram rows are hidden, not removed (`SHOW_TELEGRAM = False`, commit `409ff0a`). Restore with
`SHOW_TELEGRAM = True` or `git revert 409ff0a`.
