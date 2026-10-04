"""Messages: the mechanism. The zh-TW catalogue lives in ``locales/zh_tw_<area>.py``.

``t(msgid, english, *args)``: the English text stays at the call site (so the
wording is readable where it is used and English output never depends on a
lookup that could miss); only the zh-TW translation lives in a catalogue. A
msgid with no zh-TW entry degrades to the English text -- never a blank line,
never a crash. tests/test_i18n.py re-derives the msgids from the source, so a
missing entry, a dead entry, a ``%s`` drift or a cross-area duplicate fails CI.

``LANGUAGE`` starts from ``AGENT_TOGGLE_LANG`` (``en`` | ``zh-TW``, any case;
anything else is treated as ``en`` -- ``settings`` owns the one warning about it)
and is changed at runtime with :func:`set_language`. stdlib only; imports nothing else from the package.
"""
from __future__ import annotations

import os

from .locales import zh_tw_cli, zh_tw_common, zh_tw_config, zh_tw_picker

VALID_LANGUAGES = ("en", "zh-TW")
DEFAULT_LANGUAGE = "en"


def _merge(areas: dict[str, dict[str, str]]) -> dict[str, str]:
    """One catalogue from the per-area ones; a msgid in two areas is an error."""
    merged: dict[str, str] = {}
    owner: dict[str, str] = {}
    for area, catalog in areas.items():
        for msgid, text in catalog.items():
            if msgid in merged:
                raise RuntimeError(
                    f"i18n: msgid {msgid!r} is defined in both {owner[msgid]} and {area}")
            merged[msgid] = text
            owner[msgid] = area
    return merged


CATALOG: dict[str, str] = _merge({
    "zh_tw_cli": zh_tw_cli.CATALOG,
    "zh_tw_picker": zh_tw_picker.CATALOG,
    "zh_tw_config": zh_tw_config.CATALOG,
    "zh_tw_common": zh_tw_common.CATALOG,
})


def _match(lang: object) -> str:
    """The valid language *lang* names, case-insensitively (zh-tw == zh-TW); else en."""
    return next((v for v in VALID_LANGUAGES if v.lower() == str(lang).lower()),
                DEFAULT_LANGUAGE)


LANGUAGE = _match(os.environ.get("AGENT_TOGGLE_LANG", DEFAULT_LANGUAGE))


def set_language(lang: str) -> str:
    """Switch the language; an unknown value falls back to en. Returns the language now active."""
    global LANGUAGE
    LANGUAGE = _match(lang)
    return LANGUAGE


def t(msgid: str, english: str, *args: object) -> str:
    """zh-TW text for *msgid* when that language is active and the entry exists, else *english*.

    With no *args* the text is returned verbatim (never ``%``-formatted), so a
    literal ``%`` in a message cannot be misread as a format specifier.
    """
    text = CATALOG.get(msgid, english) if LANGUAGE != "en" else english
    return text % args if args else text
