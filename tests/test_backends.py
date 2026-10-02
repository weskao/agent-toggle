"""MCP backends: TOML text slices and ~/.claude.json scope lookup."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from base import SandboxCase

from agent_toggle import fs, mechanisms
from agent_toggle.backends import flag_json, mcp_json, mcp_toml


class TomlTest(SandboxCase):
    def test_toml_block_spans_subtables(self) -> None:
        text = (
            "[mcp_servers.alpha]\n"
            'command = "a"\n'
            "\n"
            "[mcp_servers.beta]\n"
            'command = "b"\n'
            "[mcp_servers.beta.tools.thing]\n"
            "enabled = true\n"
            "\n"
            "[other]\n"
            "k = 1\n"
        )
        span = mcp_toml.toml_block(text, "beta")
        block = "".join(text.splitlines(keepends=True)[span[0]:span[1]])
        # toml block includes its sub-tables
        self.assertTrue("beta.tools.thing" in block and "enabled = true" in block)
        # toml block stops before the next top-level table
        self.assertNotIn("[other]", block)
        # toml block excludes the previous server
        self.assertNotIn("alpha", block)
        # unknown server returns None
        self.assertIsNone(mcp_toml.toml_block(text, "nope"))

    def test_headers_subtable_is_backed_up_and_restored_verbatim(self) -> None:
        # a remote server's auth lives in a `.headers` sub-table (seen in a real grok config)
        cfg = self.home / "config.toml"
        original = (
            '[general]\nx = 1\n\n'
            '[mcp_servers.example-mcp]\nurl = "https://example.com/mcp"\n\n'
            '[mcp_servers.example-mcp.headers]\nAuthorization = "Bearer test-token-000"\n\n'
            '[other]\ny = 2\n'
        )
        cfg.write_text(original, encoding="utf-8")
        block = mcp_toml.codex_mcp_remove(cfg, "example-mcp")
        self.assertIn("Bearer test-token-000", block)
        self.assertNotIn("Bearer", cfg.read_text(encoding="utf-8"))
        self.assertIn("[other]", cfg.read_text(encoding="utf-8"))
        self.assertEqual(mechanisms.MCP_TOML_RE.findall(original), ["example-mcp"])

    def test_toml_mcp_roundtrip(self) -> None:
        cfg = self.home / "config.toml"
        cfg.write_text('[general]\nx = 1\n\n[mcp_servers.tg]\ncommand = "tg"\n'
                       '[mcp_servers.tg.tools.send]\nenabled = true\n', encoding="utf-8")
        block = mcp_toml.codex_mcp_remove(cfg, "tg")
        # removal drops the server from config
        self.assertNotIn("mcp_servers.tg", cfg.read_text(encoding="utf-8"))
        # removal keeps unrelated tables
        self.assertIn("[general]", cfg.read_text(encoding="utf-8"))
        mcp_toml.codex_mcp_add(cfg, block)
        # re-add restores the server
        self.assertIn("[mcp_servers.tg]", cfg.read_text(encoding="utf-8"))
        # re-add restores sub-tables
        self.assertIn("tg.tools.send", cfg.read_text(encoding="utf-8"))


class ClaudeJsonScopeTest(SandboxCase):
    def claude_json(self, payload: dict) -> None:
        fs.claude_json().write_text(json.dumps(payload), encoding="utf-8")

    def test_mcp_lookup_reports_scope_and_project(self) -> None:
        self.claude_json({
            "mcpServers": {"dart": {"command": "dart"}},
            "projects": {"/work/app": {"mcpServers": {"mobile": {"command": "npx"}}}},
        })
        # user-scope server is found
        self.assertEqual(mcp_json.claude_mcp_config("dart"), ({"command": "dart"}, "user", None))
        # local-scope server is found with its project
        self.assertEqual(mcp_json.claude_mcp_config("mobile"),
                         ({"command": "npx"}, "local", "/work/app"))
        # absent server is still None
        self.assertIsNone(mcp_json.claude_mcp_config("nope"))

    def test_mcp_lookup_refuses_ambiguous_local_scope(self) -> None:
        """Same name in two projects: guessing would restore it to the wrong one."""
        self.claude_json({"projects": {
            "/work/a": {"mcpServers": {"dup": {"command": "a"}}},
            "/work/b": {"mcpServers": {"dup": {"command": "b"}}},
        }})
        # ambiguous local scope is refused
        with self.assertRaisesRegex(LookupError, "2 projects"):
            mcp_json.claude_mcp_config("dup")
        state = {"version": 2, "disabled": {}}
        fails = mechanisms.toggle_mcp("disable", ["dup"], state, "claude", self.home,
                                      "claude-json")
        # ambiguity counts as a failure, not a crash
        self.assertEqual(fails, 1)
        # nothing recorded for the ambiguous server
        self.assertFalse(state["disabled"])

    def test_mcp_enable_defaults_to_user_scope_for_old_entries(self) -> None:
        """Entries parked before scope tracking have no scope field."""
        fs.backup_dir().mkdir(parents=True)
        bp = fs.backup_dir() / "claude__legacy.json"
        bp.write_text('{"command": "x"}', encoding="utf-8")
        self.cli_rc = 0
        state = {"version": 2, "disabled": {"claude:mcp:legacy": {
            "harness": "claude", "type": "mcp", "name": "legacy",
            "backend": "claude-json", "backup": str(bp), "at": "2026-01-01"}}}
        mechanisms.toggle_mcp("enable", ["legacy"], state, "claude", self.home, "claude-json")
        args, cwd = self.cli_calls[-1]
        # scopeless entry restores to user scope
        self.assertIn("user", args)
        # scopeless entry restores with no cwd
        self.assertIsNone(cwd)


FIXTURES = Path(__file__).parent / "fixtures"


class ProjectMcpJsonTest(SandboxCase):
    """The project .mcp.json helpers plan edits in the file's own layout."""

    def put(self, text: str) -> Path:
        p = self.tmp / ".mcp.json"
        p.write_bytes(text.encode("utf-8"))
        return p

    def test_remove_and_restore_keep_tabs_crlf_and_final_newline(self) -> None:
        text = '{\r\n\t"mcpServers": {\r\n\t\t"a-mcp": {"url": "u"},\r\n\t\t"b-mcp": {}\r\n\t}\r\n}\r\n'
        f = self.put(text)
        raw, before, after = mcp_json.project_mcp_remove(f, "a-mcp")
        self.assertEqual((raw, before), ({"url": "u"}, text))
        self.assertEqual(json.loads(after), {"mcpServers": {"b-mcp": {}}})
        self.assertTrue(after.endswith("}\r\n") and "\n\t" in after)
        self.assertNotIn("\n", after.replace("\r\n", ""))
        payload = {"entry": raw, "before": before, "after": after}
        f.write_bytes(after.encode("utf-8"))
        self.assertEqual(mcp_json.project_mcp_restore(f, "a-mcp", payload), (after, text))

    def test_refusals(self) -> None:
        for bad in ('{"mcpServers": {"a": 1,}}', '{"mcpServers": {"a": 1, "a": 2}}', "[]",
                    '{"mcpServers": []}', '\ufeff{"mcpServers": {}}', '{"mcpServers": {"a": NaN}}',
                    '{"mcpServers": {"a": 1e400}}', '{"mcpServers": {"a": "\\ud800"}}',
                    "[" * 100000):
            with self.assertRaises(mcp_json.ProjectMcpError, msg=bad):
                mcp_json.read_project_mcp(self.put(bad))
        with self.assertRaises(mcp_json.ProjectMcpError):
            mcp_json.read_project_mcp(self.tmp / "missing.json")
        with self.assertRaises(mcp_json.ProjectMcpError):
            mcp_json.project_mcp_remove(self.put('{"mcpServers": {}}'), "a-mcp")


class FlagJsonTest(SandboxCase):
    """flag_json flips ONE true/false token; every other byte must survive."""

    def put(self, text: str, name: str = "cfg.json") -> Path:
        p = self.tmp / name
        p.write_bytes(text.encode("utf-8"))
        return p

    def assert_one_token(self, before: bytes, after: bytes, old: bytes, new: bytes) -> None:
        i = next(i for i in range(len(before)) if before[i:i + 1] != after[i:i + 1])
        self.assertEqual(before[:i], after[:i])
        self.assertEqual(before[i:i + len(old)], old)
        self.assertEqual(after[i:i + len(new)], new)
        self.assertEqual(before[i + len(old):], after[i + len(new):])

    def flip(self, text: str, pointer: tuple[str, ...], old: str = "true") -> None:
        p = self.put(text)
        before = p.read_bytes()
        was = flag_json.set_flag(p, pointer, old != "true")
        self.assertIs(was, old == "true")
        new = "false" if old == "true" else "true"
        self.assert_one_token(before, p.read_bytes(), old.encode(), new.encode())
        self.assertIs(flag_json.read_flag(p, pointer), old != "true")
        flag_json.set_flag(p, pointer, old == "true")
        self.assertEqual(p.read_bytes(), before)

    def test_fixture_flags_round_trip_byte_identically(self) -> None:
        cases = [("openclaw/.openclaw/openclaw.json",
                  ("skills", "entries", "demo-skill", "enabled")),
                 ("openclaw/.openclaw/openclaw.json",
                  ("plugins", "entries", "demo-plugin", "enabled")),
                 ("opencode/.config/opencode/opencode.json",
                  ("mcp", "example-mcp", "enabled"))]
        for rel, pointer in cases:
            with self.subTest(file=rel, pointer=pointer):
                p = self.tmp / Path(rel).name
                shutil.copyfile(FIXTURES / rel, p)
                before = p.read_bytes()
                self.assertIs(flag_json.read_flag(p, pointer), True)
                self.assertIs(flag_json.set_flag(p, pointer, False), True)
                self.assert_one_token(before, p.read_bytes(), b"true", b"false")
                flag_json.set_flag(p, pointer, True)
                self.assertEqual(p.read_bytes(), before)

    def test_layout_variants_change_one_token_only(self) -> None:
        ptr = ("mcp", "example-mcp", "enabled")
        variants = {
            "crlf": '{\r\n  "mcp": {\r\n    "example-mcp": {"enabled": true}\r\n  }\r\n}\r\n',
            "tabs": '{\n\t"mcp":\t{\n\t\t"example-mcp" :\t{ "enabled"\t:\ttrue }\n\t}\n}',
            "bom": '\ufeff{"mcp": {"example-mcp": {"enabled": true}}}\n',
            "true in a string": '{"mcp": {"example-mcp": {"note": "enabled\\": true",'
                                ' "enabled": true}}}',
            "nested same-named key": '{"enabled": false, "mcp": {"example-mcp":'
                                     ' {"tools": {"enabled": false}, "enabled": true}}}',
            "unicode escapes": '{"m\\u0063p": {"example\\u002dmcp": {"x": "caf\\u00e9 \u2713",'
                               ' "enabled": true}}}',
            "no trailing newline, odd spacing": '{"mcp":{"example-mcp":{"enabled":true}}}',
        }
        for label, text in variants.items():
            with self.subTest(label):
                self.flip(text, ptr)

    def test_false_flag_turns_true(self) -> None:
        self.flip('{"mcp": {"example-mcp": {"enabled": false}}}',
                  ("mcp", "example-mcp", "enabled"), old="false")

    def test_setting_the_current_value_writes_nothing(self) -> None:
        p = self.put('{"a": {"enabled": true}}')
        real = fs.checked_write
        self.addCleanup(setattr, fs, "checked_write", real)
        fs.checked_write = lambda *a: self.fail("checked_write called for a no-op")
        self.assertIs(flag_json.set_flag(p, ("a", "enabled"), True), True)

    def test_missing_file_is_a_flag_error(self) -> None:
        with self.assertRaisesRegex(flag_json.FlagError, "cannot read"):
            flag_json.read_flag(self.tmp / "absent.json", ("a",))

    def test_refusals_leave_the_file_untouched(self) -> None:
        ptr = ("mcp", "example-mcp", "enabled")
        bad = {
            "missing leaf": ('{"mcp": {"example-mcp": {}}}', "not found"),
            "missing parent": ('{"mcp": {}}', "not found"),
            "parent not an object": ('{"mcp": {"example-mcp": [true]}}', "not found"),
            "not a boolean": ('{"mcp": {"example-mcp": {"enabled": 1}}}', "not a boolean"),
            "duplicate on the path": ('{"mcp": {"example-mcp": {"enabled": true,'
                                      ' "enabled": false}}}', "duplicate"),
            "duplicate elsewhere": ('{"x": 1, "x": 2, "mcp": {"example-mcp":'
                                    ' {"enabled": true}}}', "duplicate"),
            "jsonc comment": ('{\n  // comment\n  "mcp": {"example-mcp": {"enabled": true}}\n}',
                              "not strict JSON"),
            "jsonc trailing comma": ('{"mcp": {"example-mcp": {"enabled": true,}}}',
                                     "not strict JSON"),
            "json5 unquoted key": ('{mcp: {"example-mcp": {"enabled": true}}}',
                                   "not strict JSON"),
            "NaN": ('{"n": NaN, "mcp": {"example-mcp": {"enabled": true}}}',
                    "not strict JSON"),
            "top level not an object": ('[true]', "not found"),
        }
        for label, (text, msg) in bad.items():
            with self.subTest(label):
                p = self.put(text)
                with self.assertRaisesRegex(flag_json.FlagError, msg):
                    flag_json.set_flag(p, ptr, False)
                with self.assertRaisesRegex(flag_json.FlagError, msg):
                    flag_json.read_flag(p, ptr)
                self.assertEqual(p.read_bytes(), text.encode("utf-8"))

    def test_non_utf8_file_is_refused(self) -> None:
        p = self.tmp / "cfg.json"
        p.write_bytes(b'{"a": {"enabled": true}, "b": "\xff"}')
        with self.assertRaisesRegex(flag_json.FlagError, "not strict JSON"):
            flag_json.set_flag(p, ("a", "enabled"), False)

    def test_failed_verify_rolls_back_bytes(self) -> None:
        p = self.put('{"mcp": {"example-mcp": {"enabled": true}}}\n')
        before = p.read_bytes()
        real = fs._replace_bytes
        calls = []

        def corrupt_first(path, data, mode):     # the write lands damaged on disk
            calls.append(data)
            real(path, data + b"x" if len(calls) == 1 else data, mode)

        fs._replace_bytes = corrupt_first
        self.addCleanup(setattr, fs, "_replace_bytes", real)
        with self.assertRaisesRegex(fs.WriteError, "rolled back"):
            flag_json.set_flag(p, ("mcp", "example-mcp", "enabled"), False)
        self.assertEqual(p.read_bytes(), before)
        self.assertEqual(len(calls), 2)
