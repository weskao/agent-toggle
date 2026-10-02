"""MCP backends: TOML text slices and ~/.claude.json scope lookup."""
from __future__ import annotations

import dataclasses
import json
import os
import shutil
from pathlib import Path

from base import SandboxCase
from test_cli_surface import CliCase, snapshot

from agent_toggle import fs, mechanisms
from agent_toggle.backends import flag_json, mcp_json, mcp_toml
from agent_toggle.harnesses import harnesses
from agent_toggle.output import Result


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


class CopilotMcpTest(CliCase):
    """copilot's user-scope strict-JSON mcp-config.json (backend `json`)."""

    ARGS = ("mcp", "example-mcp", "--harness", "copilot")

    def setUp(self) -> None:
        super().setUp()
        shutil.copytree(FIXTURES / "copilot", self.tmp, dirs_exist_ok=True)
        self.cfg = self.tmp / ".copilot" / "mcp-config.json"
        self.bp = fs.backup_dir() / "copilot__example-mcp.json"
        self.key = "copilot:mcp:example-mcp"

    # -- helpers -----------------------------------------------------------------------
    def disable(self) -> bytes:
        """Disable example-mcp for real; returns the pre-disable bytes."""
        before = self.cfg.read_bytes()
        self.assertEqual(self.run_json("disable", *self.ARGS)[0], 0)
        return before

    def enable(self, *extra: str) -> tuple[int, dict]:
        return self.run_json("enable", *self.ARGS, *extra)

    def disabled(self) -> dict:
        return json.loads(fs.state_file().read_text(encoding="utf-8"))["disabled"]

    def retarget(self, **fields) -> None:
        """Tamper with the state entry."""
        state = json.loads(fs.state_file().read_text(encoding="utf-8"))
        state["disabled"][self.key].update(fields)
        fs.state_file().write_text(json.dumps(state), encoding="utf-8")

    def rewrite_backup(self, **fields) -> None:
        """Tamper with the backup payload fields."""
        raw = json.loads(self.bp.read_text(encoding="utf-8"))
        raw["json"].update(fields)
        self.bp.write_text(json.dumps(raw), encoding="utf-8")

    def assert_refused(self, rc_env: tuple[int, dict], needle: str, stays: bytes) -> None:
        rc, env = rc_env
        self.assertEqual((rc, env["results"][0]["status"]), (1, "error"), env)
        self.assertIn(needle, env["results"][0]["detail"])
        self.assertEqual(self.cfg.read_bytes(), stays)       # file untouched
        self.assertIn(self.key, self.disabled())             # still disabled, backup kept

    def whole_home(self) -> dict:
        return snapshot(self.tmp)

    def home_but_log(self) -> dict:
        """The home minus the audit log: a real (non-dry-run) error row is logged."""
        return {k: v for k, v in snapshot(self.tmp).items()
                if k not in (".agent-toggle", str(Path(".agent-toggle") / "log.jsonl"))}

    # -- round trips -------------------------------------------------------------------
    def test_state_entry_and_backup_payload(self) -> None:
        before = self.cfg.read_bytes()
        rc, env = self.run_json("disable", *self.ARGS)
        self.assertEqual(rc, 0, env)
        entry = self.disabled()[self.key]
        self.assertEqual((entry["backend"], entry["scope"], entry["project"],
                          entry["mechanism"]), ("json", "user", None, "remove_backup"))
        self.assertEqual(entry["backup"], str(self.bp))
        payload = json.loads(self.bp.read_text(encoding="utf-8"))["json"]
        self.assertEqual(payload["entry"]["headers"], {"Authorization": "Bearer test-token-000"})
        self.assertEqual(payload["before"], before.decode("utf-8"))
        self.assertNotIn("example-mcp", self.cfg.read_text(encoding="utf-8"))
        self.assertIn("other-mcp", self.cfg.read_text(encoding="utf-8"))
        if os.name == "posix":
            self.assertEqual(self.bp.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.enable()[0], 0)
        self.assertEqual(self.cfg.read_bytes(), before)
        self.assertEqual(self.cli_calls, [])           # never shells out to claude

    def test_enable_merges_when_the_file_changed_since_disable(self) -> None:
        self.disable()
        self.cfg.write_text('{"mcpServers": {"other-mcp": {"command": "x"}}, "extra": 1}\n',
                            encoding="utf-8")
        self.assertEqual(self.enable()[0], 0)
        cfg = json.loads(self.cfg.read_text(encoding="utf-8"))
        self.assertEqual(cfg["extra"], 1)
        self.assertEqual(cfg["mcpServers"]["other-mcp"], {"command": "x"})   # untouched kept
        self.assertEqual(cfg["mcpServers"]["example-mcp"]["url"], "https://example.invalid/mcp")

    def test_crlf_file_round_trips_byte_identical_and_merges_in_crlf(self) -> None:
        crlf = self.cfg.read_bytes().replace(b"\n", b"\r\n")
        self.cfg.write_bytes(crlf)
        self.disable()
        self.assertNotIn(b"example-mcp", self.cfg.read_bytes())
        self.assertNotIn(b"\n", self.cfg.read_bytes().replace(b"\r\n", b""))
        self.assertEqual(self.enable()[0], 0)
        self.assertEqual(self.cfg.read_bytes(), crlf)
        self.disable()                                    # now force the merge path
        self.cfg.write_bytes(b'{\r\n  "mcpServers": {},\r\n  "extra": 1\r\n}\r\n')
        self.assertEqual(self.enable()[0], 0)
        merged = self.cfg.read_bytes()
        self.assertIn(b"example-mcp", merged)
        self.assertNotIn(b"\n", merged.replace(b"\r\n", b""))     # no bare LF introduced

    # -- refusals that leave the file alone --------------------------------------------
    def test_jsonc_and_symlinked_config_are_refused_untouched(self) -> None:
        self.cfg.write_text('{\n  // machine-managed\n  "mcpServers": {"example-mcp": {}}\n}\n',
                            encoding="utf-8")
        link = self.tmp / "real.json"

        def snap() -> dict:                # the tool's own state/log dir may appear
            return {k: v for k, v in snapshot(self.tmp).items()
                    if not k.startswith(".agent-toggle")}

        before = snap()
        rc, env = self.run_json("disable", *self.ARGS)
        self.assertEqual((rc, env["results"][0]["status"]), (1, "error"))
        self.assertIn("not strict JSON", env["results"][0]["detail"])
        self.assertEqual(snap(), before)
        if os.name == "posix":
            self.cfg.rename(link)
            self.cfg.symlink_to(link)
            link.write_text('{"mcpServers": {"example-mcp": {}}}', encoding="utf-8")
            before = snap()
            rc, env = self.run_json("disable", *self.ARGS)
            self.assertEqual(rc, 1)
            self.assertIn("symlink", env["results"][0]["detail"])
            self.assertEqual(snap(), before)
        self.assertEqual(self.cli_calls, [])

    def test_oversized_config_is_refused_untouched(self) -> None:
        big = b'{"mcpServers": {"example-mcp": {}}}' + b" " * mcp_json.MAX_PROJECT_MCP
        self.cfg.write_bytes(big)
        rc, env = self.run_json("disable", *self.ARGS)
        self.assertEqual((rc, env["results"][0]["status"]), (1, "error"))
        self.assertIn("larger than", env["results"][0]["detail"])
        self.assertEqual(self.cfg.read_bytes(), big)
        self.assertNotIn(self.key, self.disabled() if fs.state_file().exists() else {})
        self.assertFalse(self.bp.exists())

    def test_disable_of_an_absent_server_changes_nothing(self) -> None:
        before = self.cfg.read_bytes()
        rc, env = self.run_json("disable", "mcp", "ghost-mcp", "--harness", "copilot")
        self.assertEqual((rc, env["results"][0]["status"]), (1, "error"))
        self.assertIn("no mcpServers.ghost-mcp", env["results"][0]["detail"])
        self.assertEqual(self.cfg.read_bytes(), before)
        self.assertFalse((fs.backup_dir() / "copilot__ghost-mcp.json").exists())
        self.assertEqual(self.disabled() if fs.state_file().exists() else {}, {})

    def test_enable_refuses_when_the_server_is_defined_again(self) -> None:
        self.disable()
        again = '{"mcpServers": {"example-mcp": {"command": "mine"}}}\n'
        self.cfg.write_text(again, encoding="utf-8")
        self.assert_refused(self.enable(), "already defined", again.encode())

    def test_enable_says_to_restore_a_vanished_key_or_file_first(self) -> None:
        self.disable()
        self.cfg.write_text('{"other": 1}\n', encoding="utf-8")
        self.assert_refused(self.enable(), "restore mcp-config.json", b'{"other": 1}\n')
        self.assertIn("mcpServers", self.enable()[1]["results"][0]["detail"])
        self.cfg.unlink()
        rc, env = self.enable()
        self.assertEqual((rc, env["results"][0]["status"]), (1, "error"))
        self.assertIn("restore mcp-config.json", env["results"][0]["detail"])
        self.assertFalse(self.cfg.exists())
        self.assertIn(self.key, self.disabled())

    # -- tampered state / backup -------------------------------------------------------
    def test_a_backup_path_pointing_at_another_file_is_refused(self) -> None:
        self.disable()
        after = self.cfg.read_bytes()
        other = fs.backup_dir() / "copilot__other-mcp.json"
        shutil.copy(self.bp, other)
        self.retarget(backup=str(other))
        self.assert_refused(self.enable(), "backup is not the one declared", after)

    def test_an_entry_that_does_not_match_its_key_or_lacks_a_backup_is_refused(self) -> None:
        self.disable()
        after = self.cfg.read_bytes()
        state = json.loads(fs.state_file().read_text(encoding="utf-8"))
        good = state["disabled"][self.key]
        for label, fields in (("name", {"name": "other-mcp"}), ("harness", {"harness": "claude"}),
                              ("type", {"type": "skill"}), ("no backup", {"backup": None})):
            with self.subTest(label):
                state["disabled"][self.key] = {**good, **fields}
                if label == "no backup":
                    del state["disabled"][self.key]["backup"]
                fs.state_file().write_text(json.dumps(state), encoding="utf-8")
                rc, env = self.enable()
                self.assertEqual((rc, env["results"][0]["status"]), (1, "error"), env)
                self.assertIn("refused", env["results"][0]["detail"])
                self.assertEqual(self.cfg.read_bytes(), after)

    def test_a_payload_with_wrong_field_types_is_refused(self) -> None:
        self.disable()
        after = self.cfg.read_bytes()
        good = json.loads(self.bp.read_text(encoding="utf-8"))["json"]
        for bad in ({"entry": []}, {"entry": "x"}, {"before": 1}, {"before": None},
                    {"after": 1}, {"after": ["a"]}):
            with self.subTest(bad=bad):
                self.bp.write_text(json.dumps({"json": {**good, **bad}}), encoding="utf-8")
                self.assert_refused(self.enable(), "is not a copilot mcp backup", after)
        for raw in ('{"json": []}', '{"other": {}}', "[]", "not json"):
            with self.subTest(raw=raw):
                self.bp.write_text(raw, encoding="utf-8")
                self.assert_refused(self.enable(), "is not a copilot mcp backup", after)

    def test_a_copilot_entry_with_another_backend_is_refused(self) -> None:
        self.disable()
        after = self.cfg.read_bytes()
        self.retarget(backend="claude-json")
        self.assert_refused(self.enable(), "refused", after)
        self.assertEqual(self.cli_calls, [])
        self.retarget(backend="toml")
        self.assert_refused(self.enable(), "refused", after)

    def test_a_tampered_before_text_is_refused_and_the_file_stays(self) -> None:
        self.disable()
        after = self.cfg.read_bytes()
        good = json.loads(self.bp.read_text(encoding="utf-8"))["json"]
        evil = good["before"].replace("example.invalid", "evil.invalid")
        ent = json.dumps(good["entry"])
        dup = '{"mcpServers": {"example-mcp": %s, "example-mcp": %s}}' % (ent, ent)
        for label, before in (
                ("other entry", evil),
                ("jsonc", '{\n// c\n"mcpServers": {"example-mcp": %s}}' % json.dumps(good["entry"])),
                ("not an object", "[]"),
                ("no server", '{"mcpServers": {}}'),
                ("servers not a dict", '{"mcpServers": []}'),
                ("duplicate key", dup),
                ("NaN", '{"mcpServers": {"example-mcp": %s, "x": NaN}}' % ent),
                ("not json", "")):
            with self.subTest(label):
                self.rewrite_backup(before=before)
                self.assert_refused(self.enable(), "is not a copilot mcp backup", after)
                snap = self.whole_home()
                self.assert_refused(self.enable("--dry-run"), "is not a copilot mcp backup",
                                    after)
                self.assertEqual(self.whole_home(), snap)

    def test_a_claude_entry_cannot_route_through_the_json_backend(self) -> None:
        self.claude_json({"mcpServers": {"x": {"command": "x"}}})
        bp = fs.backup_dir() / "claude__x.json"
        fs.private_dir(fs.backup_dir())
        bp.write_text('{"json": {"entry": {}, "before": "", "after": ""}}', encoding="utf-8")
        fs.state_file().write_text(json.dumps({"version": 3, "disabled": {"claude:mcp:x": {
            "mechanism": "remove_backup", "harness": "claude", "type": "mcp", "name": "x",
            "backend": "json", "backup": str(bp), "scope": "user", "project": None,
            "at": "2026-01-01"}}}), encoding="utf-8")
        rc, env = self.run_json("enable", "mcp", "x")
        self.assertEqual(rc, 1)
        self.assertIn("refused", env["results"][0]["detail"])
        self.assertEqual(self.cli_calls, [])

    # -- guards that the ops dispatch never trips, called directly ---------------------
    def test_toggle_mcp_fails_closed_on_json_backends_without_side_effects(self) -> None:
        for backend in ("json", "project-json"):
            for action in ("disable", "enable"):
                with self.subTest(backend=backend, action=action):
                    before = self.home_but_log()
                    out = Result()
                    fails = mechanisms.toggle_mcp(
                        action, ["example-mcp"], {"disabled": {}}, "copilot",
                        self.tmp / ".copilot", backend, out)
                    self.assertEqual(fails, 1)
                    self.assertEqual([r["status"] for r in out.rows], ["error"])
                    self.assertIn("is not handled here", out.rows[0]["detail"])
                    self.assertFalse(fs.backup_dir().exists())
                    self.assertEqual(self.home_but_log(), before)
        self.assertEqual(self.cli_calls, [])

    def test_toggle_json_mcp_refuses_a_file_outside_the_editable_set(self) -> None:
        table = dict(harnesses())
        table["copilot"] = dataclasses.replace(table["copilot"], editable=frozenset())
        before = self.home_but_log()
        out = Result()
        fails = mechanisms.toggle_json_mcp("disable", ["example-mcp"], {"disabled": {}},
                                           "copilot", out, False, table)
        self.assertEqual(fails, 1)
        self.assertIn("is not an editable copilot file", out.rows[0]["detail"])
        self.assertEqual(self.home_but_log(), before)

    # -- real --dry-run ----------------------------------------------------------------
    def test_dry_run_disable_writes_nothing_anywhere(self) -> None:
        before = self.whole_home()
        rc, env = self.run_json("disable", *self.ARGS, "--dry-run")
        self.assertEqual((rc, env["results"][0]["status"]), (0, "planned"), env)
        self.assertEqual(env["results"][0]["action"], "would-disable")
        self.assertEqual(self.whole_home(), before)       # incl. no .agent-toggle state/log
        self.assertEqual(self.cli_calls, [])

    def test_dry_run_enable_writes_nothing_anywhere(self) -> None:
        self.disable()
        before = self.whole_home()
        rc, env = self.enable("--dry-run")
        self.assertEqual((rc, env["results"][0]["status"]), (0, "planned"), env)
        self.assertEqual(env["results"][0]["action"], "would-enable")
        self.assertEqual(self.whole_home(), before)
        self.assertIn(self.key, self.disabled())
        self.assertEqual(self.cli_calls, [])


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
