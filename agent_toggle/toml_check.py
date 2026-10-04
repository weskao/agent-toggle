"""Structural TOML 1.0 check for Python 3.10 (no tomllib; stdlib only).

parse(text) validates the subset codex/grok config.toml use -- comments, [tables],
[[arrays of tables]], bare/quoted/dotted keys, strings (basic, literal, multi-line),
numbers, booleans, dates, arrays, inline tables -- and rejects duplicate tables/keys.
It returns a tree of Table (dict) nodes; values are not decoded (leaves are True), so
it is for "does it parse / is this table there", never for reading values. Raises
ValueError. Tests cross-check it against tomllib so it cannot drift looser.
"""
from __future__ import annotations

import re

_BARE = re.compile(r"[A-Za-z0-9_-]+")
_WS = " \t"
_END = r"(?=[ \t\r\n,\]}#]|$)"
_DIG = r"\d(?:_?\d)*"
_INT = r"[+-]?(?:0|[1-9](?:_?\d)*)"
_VALUE = re.compile("(?:" + "|".join((
    r"\d{4}-\d\d-\d\d(?:[Tt ]\d\d:\d\d:\d\d(?:\.\d+)?(?:[Zz]|[+-]\d\d:\d\d)?)?",
    r"\d\d:\d\d:\d\d(?:\.\d+)?",
    rf"{_INT}(?:\.{_DIG}(?:[eE][+-]?{_DIG})?|[eE][+-]?{_DIG})",
    rf"{_INT}", r"0x[0-9A-Fa-f](?:_?[0-9A-Fa-f])*", r"0o[0-7](?:_?[0-7])*", r"0b[01](?:_?[01])*",
    r"[+-]?(?:inf|nan)", r"true", r"false")) + ")" + _END)
_ESC = {"b", "t", "n", "f", "r", '"', "\\"}


class Table(dict):
    """kind: "explicit" ([header]), "implicit" (parent of a header), "dotted" (a.b = 1),
    "closed" (inline table: no later additions)."""
    kind = "implicit"

    def __init__(self, kind: str = "implicit") -> None:
        super().__init__()
        self.kind = kind


class _P:
    def __init__(self, s: str) -> None:
        self.s, self.i = s, 0

    def err(self, why: str) -> ValueError:
        line = self.s.count("\n", 0, self.i) + 1
        return ValueError(f"invalid TOML at line {line}: {why}")

    def peek(self, n: int = 1) -> str:
        return self.s[self.i:self.i + n]

    def ws(self) -> None:
        while self.peek() and self.peek() in _WS:
            self.i += 1

    def comment(self) -> None:
        if self.peek() == "#":
            while self.peek() not in ("", "\n", "\r"):
                c = self.s[self.i]
                if (c < " " and c != "\t") or c == "\x7f":
                    raise self.err("control character in comment")
                self.i += 1

    def newline(self) -> bool:
        if self.peek() == "\n":
            self.i += 1
        elif self.peek(2) == "\r\n":
            self.i += 2
        else:
            return False
        return True

    def skip_all(self) -> None:           # whitespace, comments, newlines
        while True:
            self.ws()
            self.comment()
            if not self.newline():
                return

    def eol(self) -> None:
        self.ws()
        self.comment()
        if self.peek() and not self.newline():
            raise self.err("expected end of line")

    # --- keys and strings ---
    def key(self) -> list[str]:
        parts = []
        while True:
            self.ws()
            if self.peek() in ('"', "'"):
                parts.append(self.string(multi=False))
            elif m := _BARE.match(self.s, self.i):
                parts.append(m.group())
                self.i = m.end()
            else:
                raise self.err("expected a key")
            self.ws()
            if self.peek() != ".":
                return parts
            self.i += 1

    def string(self, multi: bool = True) -> str:
        q = self.s[self.i]
        triple = multi and self.peek(3) == q * 3
        self.i += 3 if triple else 1
        start = self.i                    # raw text, escapes undecoded: enough to spot a repeated key
        while True:
            c = self.peek()
            if not c:
                raise self.err("unterminated string")
            if c == q:
                if not triple:
                    self.i += 1
                    return self.s[start:self.i - 1]
                n = len(re.compile(re.escape(q) + "+").match(self.s, self.i).group())
                if n >= 3:
                    if n > 5:
                        raise self.err("too many quotes")
                    self.i += n
                    return self.s[start:self.i - 3]
                self.i += n
                continue
            if c in "\r\n" and not triple:
                raise self.err("newline in string")
            if (c < " " and c not in "\t\n\r") or c == "\x7f" or (c == "\r" and self.peek(2) != "\r\n"):
                raise self.err("control character in string")
            if c == "\\" and q == '"':
                n = self.s[self.i + 1:self.i + 2]
                if n in _ESC:
                    self.i += 2
                elif n in ("u", "U"):
                    width = 4 if n == "u" else 8
                    h = self.s[self.i + 2:self.i + 2 + width]
                    if len(h) != width or not re.fullmatch(r"[0-9A-Fa-f]+", h) or int(h, 16) > 0x10FFFF \
                            or 0xD800 <= int(h, 16) <= 0xDFFF:
                        raise self.err("bad unicode escape")
                    self.i += 2 + width
                elif triple and (m := re.compile(r"[ \t]*\r?\n[ \t\r\n]*").match(self.s, self.i + 1)):
                    self.i = m.end()
                else:
                    raise self.err("bad escape")
                continue
            self.i += 1

    # --- values ---
    def value(self):
        c = self.peek()
        if c in ('"', "'"):
            self.string()
        elif c == "[":
            self.array()
        elif c == "{":
            return self.inline()
        elif m := _VALUE.match(self.s, self.i):
            self.i = m.end()
        else:
            raise self.err("bad value")
        return True

    def array(self) -> None:
        self.i += 1
        while True:
            self.skip_all()
            if self.peek() == "]":
                break
            self.value()
            self.skip_all()
            if self.peek() == ",":
                self.i += 1
            elif self.peek() != "]":
                raise self.err("expected , or ] in array")
        self.i += 1

    def inline(self) -> Table:
        self.i += 1
        t = Table("closed")
        self.ws()
        if self.peek() == "}":
            self.i += 1
            return t
        while True:
            self.keyval(t)
            self.ws()
            c = self.peek()
            self.i += 1
            if c == "}":
                return t
            if c != ",":
                raise self.err("expected , or } in inline table")

    def keyval(self, table: Table) -> None:
        path = self.key()
        self.ws()
        if self.peek() != "=":
            raise self.err("expected =")
        self.i += 1
        self.ws()
        for k in path[:-1]:
            sub = table.setdefault(k, Table("dotted"))
            if not isinstance(sub, Table) or sub.kind not in ("dotted",):
                raise self.err(f"key {k!r} already defined")
            table = sub
        if path[-1] in table:
            raise self.err(f"duplicate key {path[-1]!r}")
        table[path[-1]] = self.value()

    # --- headers ---
    def header(self, root: Table) -> Table:
        aot = self.peek(2) == "[["
        self.i += 2 if aot else 1
        path = self.key()
        close = "]]" if aot else "]"
        if self.peek(len(close)) != close:
            raise self.err("bad table header")
        self.i += len(close)
        t = root
        for k in path[:-1]:
            sub = t.setdefault(k, Table())
            if isinstance(sub, list):
                sub = sub[-1]
            if not isinstance(sub, Table) or sub.kind == "closed":
                raise self.err(f"table {k!r} already defined as a value")
            t = sub
        last = path[-1]
        old = t.get(last)
        if aot:
            if old is None:
                old = t[last] = []
            elif not isinstance(old, list):
                raise self.err(f"duplicate table {last!r}")
            old.append(Table("explicit"))
            return old[-1]
        if old is None:
            old = t[last] = Table("explicit")
        elif isinstance(old, Table) and old.kind == "implicit":
            old.kind = "explicit"
        else:
            raise self.err(f"duplicate table {last!r}")
        return old


def parse(text: str) -> Table:
    p = _P(text)
    root = cur = Table("explicit")
    while True:
        p.skip_all()
        if not p.peek():
            return root
        if p.peek() == "[":
            cur = p.header(root)
        else:
            p.keyval(cur)
        p.eol()
