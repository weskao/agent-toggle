"""Boolean flag in a JSON config (DESIGN s4, s5.5): openclaw skills/plugins, opencode mcp.

The edit is TEXT-LEVEL: walk to the value span and replace only the `true` /
`false` token, so comments-free formatting, key order, escapes, CRLF, tabs and
a BOM all survive byte for byte. Never json.dump the whole file.

Decisions:
- JSON or JSONC (`//` and `/* */` comments, trailing commas: opencode.json).
  JSONC is parsed through strip_jsonc, a LENGTH-PRESERVING mask (comments and
  trailing commas blanked to spaces, newlines kept, strings untouched), so the
  walk's offsets are offsets into the original text and the one-token edit is
  applied there: comments, commas and layout survive byte for byte.
- JSON5 (unquoted keys, single quotes, hex, NaN/Infinity -- the openclaw config
  may be JSON5) is REFUSED (FlagError), never rewritten: the mask cannot make
  it JSON, and a re-serialise would drop the user's comments and formatting.
- A missing pointer is refused: we never invent a key, because an absent key
  means the harness default, which we cannot know.
- A duplicated key anywhere in the file is refused: which copy the harness
  honours is parser-specific, so flipping either one could be a no-op.
- The shapes `skills.entries.<name>.enabled`, `plugins.entries.<name>.enabled`
  (openclaw) and `mcp.<name>.enabled` (opencode) are ASSUMED from DESIGN s4/s11,
  not verified on a real install.
"""
from __future__ import annotations

import json
import re
from json.decoder import WHITESPACE, scanstring
from pathlib import Path
from typing import Sequence

from agent_toggle import fs

_DECODER = json.JSONDecoder()
BOM = "\ufeff"


# a string (kept) | a comment (blanked); then a string | a trailing comma.
# ponytail: quadratic on malformed input only (many unclosed `/*`); fine at config
# size, a hand-written scanner if a multi-MB hostile config ever matters.
_COMMENT = re.compile(r'"(?:\\.|[^"\\])*"|//[^\r\n]*|/\*.*?\*/', re.S)
_COMMA = re.compile(r'"(?:\\.|[^"\\])*"|,(?=[ \t\r\n]*[}\]])')


def _blank(m: re.Match) -> str:
    return m[0] if m[0][0] == '"' else re.sub(r"[^\r\n]", " ", m[0])


def strip_jsonc(text: str) -> str:
    """`text` with JSONC comments and trailing commas blanked to spaces (newlines
    kept). Same length, so every index maps to the same char in `text`. A `//`
    or `/*` inside a string is not a comment. Anything else non-JSON (JSON5) is
    left alone and still fails json.loads."""
    return _COMMA.sub(_blank, _COMMENT.sub(_blank, text))


def jsonc_loads(text: str, **kw):
    """json.loads for JSON or JSONC text (a leading BOM is ignored)."""
    return json.loads(strip_jsonc(text).removeprefix(BOM), **kw)


class FlagError(ValueError):
    """The flag cannot be read or set safely; the message says why."""


class FlagMissing(FlagError):
    """The file or the pointer does not exist (as opposed to an unsafe file)."""


def _no_dupes(pairs):
    seen = set()
    for k, _ in pairs:
        if k in seen:
            raise FlagError(f"duplicate key {k!r}")
        seen.add(k)
    return dict(pairs)


def _no_constant(name):
    raise ValueError(f"{name} is not valid JSON")


def _strict(text: str, where: str) -> None:
    """Parse as JSON or JSONC (no NaN, duplicate keys or other JSON5)."""
    try:
        jsonc_loads(text, object_pairs_hook=_no_dupes, parse_constant=_no_constant)
    except FlagError as e:
        raise FlagError(f"{where}: {e}; refusing to edit") from None
    except (ValueError, RecursionError) as e:     # 3.10-3.12 recurse on deep nesting
        raise FlagError(f"{where}: not strict JSON or JSONC (JSON5 is not supported): {e}") \
            from None


def _ws(text: str, i: int) -> int:
    return WHITESPACE.match(text, i).end()


def _span(text: str, pointer: Sequence[str], where: str) -> tuple[int, int, bool]:
    """(start, end, value) of the boolean token at `pointer`. `text` must already
    have passed _strict and been through strip_jsonc, so the walk only has to
    find, not validate; the offsets are valid in the unmasked text too."""
    shown = ".".join(pointer)
    if not pointer:
        raise FlagError(f"{where}: empty pointer")
    i = _ws(text, 1 if text.startswith(BOM) else 0)
    for depth, want in enumerate(pointer):
        if text[i:i + 1] != "{":
            raise FlagMissing(f"{where}: {shown} not found")
        i = _ws(text, i + 1)
        while True:
            if text[i] == "}":
                raise FlagMissing(f"{where}: {shown} not found")
            key, i = scanstring(text, i + 1)         # text[i] is the opening quote
            i = _ws(text, _ws(text, i) + 1)          # past ':'
            if key == want:
                break
            _, i = _DECODER.raw_decode(text, i)      # skip this value
            i = _ws(text, i)
            if text[i] == ",":
                i = _ws(text, i + 1)
    for token, value in (("true", True), ("false", False)):
        if text.startswith(token, i):
            return i, i + len(token), value
    raise FlagError(f"{where}: {shown} is not a boolean")


def _load(file: Path) -> str:
    try:
        text = Path(file).read_bytes().decode("utf-8")
    except UnicodeDecodeError as e:
        raise FlagError(f"{file}: not strict JSON (not UTF-8: {e})") from None
    except OSError as e:                         # missing / unreadable: a row, not a traceback
        raise (FlagMissing if isinstance(e, FileNotFoundError) else FlagError)(f"{file}: cannot read ({e.strerror or e})") from None
    _strict(text, str(file))
    return text


def read_flag(file: Path, pointer: Sequence[str]) -> bool:
    """The boolean at `pointer` (a sequence of object keys) in `file`."""
    text = _load(file)
    return _span(strip_jsonc(text), pointer, str(file))[2]


def set_flag(file: Path, pointer: Sequence[str], value: bool) -> bool:
    """Set the boolean at `pointer` to `value`; return the previous value.

    Same value -> no write. Otherwise written through fs.checked_write; the
    invariant is that the file reads back as exactly `before` with only that
    token swapped, still JSON/JSONC, with the new value at `pointer`. Any
    mismatch (including a concurrent edit since our read) rolls back and
    raises fs.WriteError.
    """
    where = str(file)
    text = _load(file)
    start, end, was = _span(strip_jsonc(text), pointer, where)
    if was is bool(value):
        return was
    expected = text[:start] + ("true" if value else "false") + text[end:]

    def verify(before: str, after: str) -> str:
        if before != text:
            raise ValueError("file changed since it was read")
        if after != expected:
            raise ValueError("result differs from the one-token edit")
        _strict(after, where)
        if _span(strip_jsonc(after), pointer, where)[2] is not bool(value):
            raise ValueError("flag did not take the new value")
        return ""

    fs.checked_write(Path(file), expected, verify)
    return was
