"""safe_move: the guard against `mv X dest/` renaming X to dest."""
from __future__ import annotations

import os
import random
import unittest
from unittest import mock

from base import CAN_SYMLINK, SandboxCase

from agent_toggle import fs, toml_check
from test_conformance import FIXTURES


class SafeMoveTest(SandboxCase):
    def test_never_renames_source_into_dest(self) -> None:
        """The bug this guard exists for: `mv X dest/` renames X to dest."""
        src = self.write("skills/demo/SKILL.md", "hello")
        dest = self.home / "skills-disabled"
        self.assertFalse(dest.exists())
        fs.safe_move(src.parent, dest)
        # safe_move keeps the item as a CHILD of dest
        self.assertEqual((dest / "demo" / "SKILL.md").read_text(encoding="utf-8"), "hello")
        # safe_move did not rename src to dest
        self.assertTrue(dest.is_dir())

    def test_refuses_file_as_dest_dir(self) -> None:
        src = self.write("skills/a/SKILL.md")
        blocker = self.write("skills-disabled", "i am a file")
        with self.assertRaises(NotADirectoryError):
            fs.safe_move(src.parent, blocker)

    def test_refuses_overwrite(self) -> None:
        src = self.write("skills/a/SKILL.md", "new")
        self.write("skills-disabled/a/SKILL.md", "old")
        with self.assertRaises(FileExistsError):
            fs.safe_move(src.parent, self.home / "skills-disabled")
        # existing parked copy untouched
        self.assertEqual((self.home / "skills-disabled/a/SKILL.md").read_text(encoding="utf-8"), "old")


POSIX = os.name != "nt"


class StateSubdirsTest(SandboxCase):
    def test_profiles_and_parked_live_under_state_dir(self) -> None:
        self.assertEqual(fs.profiles_dir(), fs.state_dir() / "profiles")
        self.assertEqual(fs.parked_dir(), fs.state_dir() / "parked")


class ContainedTest(SandboxCase):
    def test_item_inside_root_is_contained(self) -> None:
        item = self.write("skills/demo-skill/SKILL.md").parent
        self.assertTrue(fs.contained(item, self.home / "skills"))
        # nested item, second root
        self.assertTrue(fs.contained(item / "SKILL.md", self.home / "agents", self.home / "skills"))

    def test_outside_every_root_is_refused(self) -> None:
        self.assertFalse(fs.contained(self.tmp / "elsewhere" / "x", self.home / "skills"))

    def test_dotdot_is_refused_even_when_it_lands_inside(self) -> None:
        self.write("skills/demo-skill/SKILL.md")
        self.assertFalse(fs.contained(self.home / "skills" / "a" / ".." / "demo-skill",
                                      self.home / "skills"))
        self.assertFalse(fs.contained(self.home / "skills" / ".." / ".." / "x",
                                      self.home / "skills"))

    @unittest.skipUnless(CAN_SYMLINK, "cannot create symlinks here")
    def test_symlinked_parent_escaping_the_root_is_refused(self) -> None:
        outside = self.tmp / "outside"
        (outside / "demo-skill").mkdir(parents=True)
        (self.home / "skills" / "link").symlink_to(outside)
        self.assertFalse(fs.contained(self.home / "skills" / "link" / "demo-skill",
                                      self.home / "skills"))

    @unittest.skipUnless(CAN_SYMLINK, "cannot create symlinks here")
    def test_item_symlink_is_not_followed(self) -> None:
        outside = self.tmp / "outside"
        outside.mkdir()
        (self.home / "skills" / "demo-skill").symlink_to(outside)
        self.assertTrue(fs.contained(self.home / "skills" / "demo-skill", self.home / "skills"))

    @unittest.skipUnless(CAN_SYMLINK, "cannot create symlinks here")
    def test_parked_dir_reached_through_a_symlink_is_refused(self) -> None:
        real = self.tmp / "real-state"
        (real / "parked").mkdir(parents=True)
        fs.state_dir().symlink_to(real)
        self.assertFalse(fs.contained(fs.parked_dir() / "demo-skill", fs.parked_dir()))
        # the parked dir itself as a symlink too
        fs.state_dir().unlink()
        fs.state_dir().mkdir()
        fs.parked_dir().symlink_to(real / "parked")
        self.assertFalse(fs.contained(fs.parked_dir() / "demo-skill", fs.parked_dir()))

    def test_parked_dir_ok(self) -> None:
        fs.parked_dir().mkdir(parents=True)
        self.assertTrue(fs.contained(fs.parked_dir() / "claude" / "demo-skill", fs.parked_dir()))


class CheckedWriteTest(SandboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.cfg = self.write("config.json", "")
        self.cfg.write_bytes(b'{"a": 1}\n')    # write_text would turn \n into \r\n on Windows
        if POSIX:
            self.cfg.chmod(0o640)

    def test_ok_write_lands_and_keeps_mode(self) -> None:
        note = fs.checked_write(self.cfg, '{"a": 2}\n', fs.json_verify())
        self.assertEqual(note, "")
        self.assertEqual(self.cfg.read_bytes(), b'{"a": 2}\n')
        if POSIX:
            self.assertEqual(self.cfg.stat().st_mode & 0o777, 0o640)

    def test_failed_verify_restores_bytes(self) -> None:
        with self.assertRaises(fs.WriteError) as cm:
            fs.checked_write(self.cfg, "{not json", fs.json_verify())
        self.assertIn(self.cfg.name, str(cm.exception))   # 8.3 vs long temp path on Windows
        self.assertEqual(self.cfg.read_bytes(), b'{"a": 1}\n')
        self.assertEqual(list(self.home.glob(".config.json.*")), [])   # no tmp left

    @unittest.skipIf(not POSIX, "POSIX modes")
    def test_failed_verify_restores_mode(self) -> None:
        def chmod_then_fail(before: str, after: str) -> str:
            self.cfg.chmod(0o600)          # something changed the mode mid-write
            raise ValueError("boom")
        with self.assertRaises(fs.WriteError):
            fs.checked_write(self.cfg, '{"a": 3}\n', chmod_then_fail)
        self.assertEqual(self.cfg.read_bytes(), b'{"a": 1}\n')
        self.assertEqual(self.cfg.stat().st_mode & 0o777, 0o640)

    def test_failed_write_of_a_new_file_leaves_nothing(self) -> None:
        new = self.home / "new.json"
        with self.assertRaises(fs.WriteError):
            fs.checked_write(new, "{not json", fs.json_verify())
        self.assertFalse(new.exists())

    @unittest.skipUnless(CAN_SYMLINK, "cannot create symlinks here")
    def test_symlinked_config_is_written_through(self) -> None:
        link = self.home / "linked.json"
        link.symlink_to(self.cfg)
        fs.checked_write(link, '{"a": 2}\n', fs.json_verify())
        self.assertTrue(link.is_symlink())
        self.assertEqual(self.cfg.read_bytes(), b'{"a": 2}\n')
        with self.assertRaises(fs.WriteError):
            fs.checked_write(link, "{bad", fs.json_verify())
        self.assertTrue(link.is_symlink())
        self.assertEqual(self.cfg.read_bytes(), b'{"a": 2}\n')

    def test_interrupt_mid_verify_still_rolls_back(self) -> None:
        def interrupted(before: str, after: str) -> str:
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            fs.checked_write(self.cfg, '{"a": 9}\n', interrupted)
        self.assertEqual(self.cfg.read_bytes(), b'{"a": 1}\n')

    def test_a_failed_first_write_is_not_reported_as_a_failed_restore(self) -> None:
        with mock.patch.object(fs, "_replace_bytes", side_effect=PermissionError("read-only dir")):
            with self.assertRaises(fs.WriteError) as cm:
                fs.checked_write(self.cfg, '{"a": 2}\n', fs.json_verify())
        self.assertNotIn("restore failed", str(cm.exception))
        self.assertIn("rolled back", str(cm.exception))
        self.assertEqual(self.cfg.read_bytes(), b'{"a": 1}\n')

    def test_verify_sees_before_and_after(self) -> None:
        seen = []
        fs.checked_write(self.cfg, "{}", lambda b, a: seen.append((b, a)) or "")
        self.assertEqual(seen, [('{"a": 1}\n', "{}")])


class VerifyTest(unittest.TestCase):
    BEFORE = '[mcp_servers.example-mcp]\ncommand = "demo"\n\n[other]\nx = 1\n'
    AFTER = '[other]\nx = 1\n'

    def test_json_verify(self) -> None:
        self.assertEqual(fs.json_verify()("{}", '{"a": 1}'), "")
        with self.assertRaises(ValueError):
            fs.json_verify()("{}", "{nope")

    def test_json_verify_expected_is_byte_for_byte(self) -> None:
        self.assertEqual(fs.json_verify('{"a": true}')("{}", '{"a": true}'), "")
        with self.assertRaises(ValueError):
            fs.json_verify('{"a": true}')("{}", '{"a":true}')

    @unittest.skipIf(fs._tomllib() is None, "needs tomllib (3.11+)")
    def test_toml_verify_parses_with_tomllib(self) -> None:
        self.assertEqual(fs.toml_verify()(self.BEFORE, self.AFTER), "")
        with self.assertRaises(ValueError):
            fs.toml_verify()(self.BEFORE, "[broken\n")
        with self.assertRaises(ValueError):
            fs.toml_verify(self.AFTER)(self.BEFORE, self.AFTER + "y = 2\n")

    def test_toml_verify_without_tomllib_is_textual_and_parsed(self) -> None:
        with mock.patch.object(fs, "_tomllib", lambda: None):
            self.assertEqual(fs.toml_verify(self.AFTER)(self.BEFORE, self.AFTER), "")
            with self.assertRaises(ValueError):
                fs.toml_verify(self.AFTER)(self.BEFORE, self.AFTER + "y = 2\n")
            self.assertEqual(fs.toml_verify()(self.BEFORE, self.AFTER), "")
            with self.assertRaises(ValueError):
                fs.toml_verify()(self.BEFORE, "[broken\n")
            with self.assertRaises(ValueError):          # parse check runs even with `expected`
                fs.toml_verify("[other]\nx = 1\nx = 2\n")(self.BEFORE, "[other]\nx = 1\nx = 2\n")


VALID_TOML = [
    "", "# only a comment\n", "a = 1", "a = 1 # trailing\r\nb = 2\r\n",
    "[a]\n[a.b]\n[a.b.c]\nx = 1\n", "[a.b]\n[a]\nx = 1\n", '[a."q.k".\'l\']\nx = 1\n',
    "[[t]]\nx = 1\n[[t]]\nx = 2\n[t.sub]\ny = 3\n[[t.list]]\n",
    'a.b.c = 1\na.b.d = 2\n"q k" = 1\n\'l\' = 2\n1234 = 3\n',
    '[f]\napple.color = "red"\n[f.apple.texture]\nsmooth = true\n',
    's = "a \\"q\\" \\u00e9 \\U0001F600 \\\\"\nl = \'C:\\\\x\'\ne = ""\nf = \'\'\n',
    's = """\nline one\n  two "quoted" ""\\\n   joined"""\n',
    "s = \'\'\'\nraw \\ \'x\' \'\'\nend\'\'\'\'\'\n",
    "i = [0, +1, -2, 1_000, 0xDEAD_beef, 0o17, 0b101]\nf = [1.5, -0.1, 6.0e2, 1e-3, 1_0.2_5, inf, -inf, nan]\n",
    "t = [true, false]\nd = [1979-05-27, 07:32:00, 1979-05-27T07:32:00Z, 1979-05-27 07:32:00.5-07:00]\n",
    "a = [\n  1, # one\n  2,\n]\nn = [[1, 2], [\"x\", [true]], []]\nm = [{x = 1}, {y = [1]}]\n",
    "i = {a = 1, b.c = 2, d = {e = [1, 2]}}\ne = {}\n",
    '[mcp_servers.x]\ncommand = "npx"\nargs = ["-y", "pkg"]\nenv = { TOKEN = "t" }\n',
    "[a.b.c]\n[a]\nb.d = 1\n", "[t.a.c]\n[t]\na.d = 1\n",                  # dotted key into an implicit table
    '"a\\\\u0041" = 1\naA = 2\n\'x\\ty\' = 3\n"x\\ty" = 4\n',            # decoded keys that differ
    "d = [2024-02-29, 23:59:59, 2024-02-29T00:00:00+23:59, 1979-05-27 07:32:00.5-07:00]\n",
]
BAD_TOML = {
    "unterminated string": 'a = "abc\n',
    "unterminated multiline": 'a = """abc\n',
    "unterminated literal": "a = 'abc\nb = 1\n",
    "newline in basic string": 'a = "ab\ncd"\n',
    "bad escape": 'a = "\\q"\n',
    "bad unicode escape": 'a = "\\u12"\n',
    "bad header": "[a\nx = 1\n",
    "empty header": "[]\n",
    "header trailing junk": "[a] x = 1\n",
    "mismatched aot header": "[[a]\n",
    "duplicate key": "a = 1\na = 2\n",
    "duplicate dotted key": "a.b = 1\na.b = 2\n",
    "key vs dotted table": "a = 1\na.b = 2\n",
    "duplicate table": "[a]\nx = 1\n[a]\ny = 2\n",
    "duplicate nested table": "[a.b]\n[a.b]\n",
    "table vs key": "a = 1\n[a]\n",
    "aot vs table": "[a]\n[[a]]\n",
    "table vs aot": "[[a]]\n[a]\n",
    "extend inline table": "a = {x = 1}\n[a.y]\n",
    "dotted table redefined": "[f]\na.b = 1\n[f.a]\n",
    "missing equals": "a 1\n",
    "missing value": "a =\n",
    "key only": "a\n",
    "two values": "a = 1 2\n",
    "bad number": "a = 1.\nb = 01\n",
    "bad bool": "a = True\n",
    "unquoted string": "a = abc\n",
    "unterminated array": "a = [1, 2\n",
    "array missing comma": "a = [1 2]\n",
    "unterminated inline": "a = {x = 1\n",
    "inline trailing comma": "a = {x = 1,}\n",
    "inline newline": "a = {x = 1,\ny = 2}\n",
    "inline duplicate": "a = {x = 1, x = 2}\n",
    "bad date": "a = 1979-13\n",
    "month 13": "a = 1979-13-01\n", "feb 30": "a = 2024-02-30\n", "feb 29 off-year": "a = 2023-02-29\n",
    "day 00": "a = 2024-01-00\n", "hour 24": "a = 24:00:00\n", "minute 60": "a = 12:60:00\n",
    "second 60": "a = 12:00:60\n", "datetime hour 24": "a = 2024-01-01T24:00:00Z\n",
    "offset hour 24": "a = 2024-01-01T00:00:00+24:00\n", "offset minute 60": "a = 2024-01-01T00:00:00+00:60\n",
    "bare cr after line-ending backslash": 's = """a\\\n \r b"""\n',
    "dotted into explicit table": "[a.b]\n[a]\nb.d = 1\n",
    "dotted into explicit nested": "[a.b.c]\n[a]\nb.c.d = 1\n",
    "dotted into inline table": "a = {x = 1}\na.y = 2\n",
    "dotted-through table reopened": "[a.b.c]\n[a]\nb.d = 1\n[a.b]\n",
    "quoted dup of bare": '"a" = 1\na = 2\n', "literal dup of bare": "'a' = 1\na = 2\n",
    "escape-equal keys": '"a\\tb" = 1\n"a\\u0009b" = 2\n',
    "long escape-equal keys": '"\\U00000041" = 1\nA = 2\n',
    "quoted header dup": '[a]\n["a"]\n', "quoted dotted dup": '[t]\n"x".y = 1\nx.y = 2\n',
    "escaped backslash key dup": '"a\\\\b" = 1\n\'a\\b\' = 2\n',
    "control char": 'a = "x\x00y"\n',
    "bare cr": "a = 1\rb = 2\n",
}


class TomlCheckTest(unittest.TestCase):
    """The Python 3.10 fallback validator, forced on, and (on 3.11+) held to tomllib."""

    def test_accepts_the_codex_and_grok_fixtures(self) -> None:
        for f in FIXTURES.glob("*/.*/config.toml"):
            with self.subTest(f=str(f)):
                tree = toml_check.parse(f.read_text(encoding="utf-8"))
                self.assertIn("example-mcp", tree["mcp_servers"])

    def test_accepts_valid_snippets(self) -> None:
        for text in VALID_TOML:
            with self.subTest(text=text):
                toml_check.parse(text)

    def test_rejects_malformed_snippets(self) -> None:
        for why, text in BAD_TOML.items():
            with self.subTest(why=why), self.assertRaises(ValueError):
                toml_check.parse(text)

    def test_toml_parse_falls_back_when_tomllib_is_missing(self) -> None:
        with mock.patch.object(fs, "_tomllib", lambda: None):
            self.assertIn("a", fs.toml_parse("[a.b]\n"))
            with self.assertRaises(ValueError):
                fs.toml_parse("[a]\n[a]\n")

    @unittest.skipIf(fs._tomllib() is None, "needs tomllib (3.11+)")
    def test_never_looser_than_tomllib(self) -> None:
        real = fs._tomllib()
        texts = [f.read_text(encoding="utf-8") for f in FIXTURES.glob("*/.*/config.toml")]
        for text in texts + VALID_TOML:
            with self.subTest(text=text):
                real.loads(text)
                toml_check.parse(text)       # accepted by both
        for why, text in BAD_TOML.items():
            with self.subTest(why=why), self.assertRaises(ValueError):
                real.loads(text)             # the snippet really is invalid TOML

    @unittest.skipIf(fs._tomllib() is None, "needs tomllib (3.11+)")
    def test_mutations_agree_with_tomllib(self) -> None:
        """Seeded random edits of known-good TOML: the fallback accepts exactly what tomllib does."""
        real = fs._tomllib()
        seeds = VALID_TOML + [f.read_text(encoding="utf-8") for f in sorted(FIXTURES.glob("*/.*/config.toml"))]
        seeds += list(BAD_TOML.values())
        rng, alphabet = random.Random(20261004), "\"'\\[]{}=.,#\n\r \t0189aAeEuUxT-+:_Z234567"
        seen = set()
        for _ in range(2000):
            text = rng.choice(seeds)
            for _ in range(rng.randint(1, 3)):
                i = rng.randint(0, len(text))
                op = rng.randrange(5)
                if op == 0:
                    text = text[:i] + rng.choice(alphabet) + text[i:]
                elif op == 1:
                    text = text[:i] + text[i + 1:]
                elif op == 2:
                    text = text[:i] + text[i:i + 1] * 2 + text[i + 1:]
                elif op == 3:
                    lines = text.splitlines(keepends=True) or [""]
                    j = rng.randrange(len(lines))
                    lines.insert(j, lines[rng.randrange(len(lines))])
                    text = "".join(lines)
                else:
                    text = text.replace('"', "'") if rng.random() < .5 else text.replace("'", '"')
            if text in seen:
                continue
            seen.add(text)
            try:
                real.loads(text)
                want = True
            except ValueError:
                want = False
            try:
                toml_check.parse(text)
                got = True
            except ValueError:
                got = False
            self.assertEqual(got, want, f"fallback={got} tomllib={want} for {text!r}")
