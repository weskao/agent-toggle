"""zh-TW catalogues, one module per area so concurrent edits never touch one file.

Each ``zh_tw_<area>`` module exposes ``CATALOG = {msgid: zh-TW text}``;
``agent_toggle.i18n`` merges them and refuses to import if a msgid is in two.
"""
