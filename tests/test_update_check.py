"""PyPI update hint -- stdlib checker.

Never hits the network: every test injects ``fetch`` / ``now`` / ``run``. A
production change that always returns None, that treats 0.10.0 as not newer
than 0.9.1, or that fetches on every call despite a fresh cache, is what
these catch. Ported from aicp's tests/test_update_check.py.
"""
from __future__ import annotations

import ast
import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from agent_toggle import update_check

DIST = "agent-toggle"


def _pypi(version: str) -> str:
    return json.dumps({"info": {"version": version}})


class _Done:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


def _offline(_dist: str, _timeout: float) -> str:
    raise OSError("offline")


class UpdateCheckCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="agent-toggle-update-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cache = self.tmp / "update-check.json"

    def check(self, *, current="0.9.1", latest="0.10.0", now=1_000_000.0, ttl=86_400,
              fetch=None, calls=None):
        def _fetch(dist: str, timeout: float) -> str:
            if calls is not None:
                calls.append((dist, timeout))
            return fetch(dist, timeout) if fetch is not None else _pypi(latest)

        return update_check.check(DIST, current, cache_path=self.cache, ttl_seconds=ttl,
                                  timeout=0.8, now=now, fetch=_fetch)

    def seed(self, **data) -> None:
        self.cache.write_text(json.dumps(data), encoding="utf-8")


class CheckTest(UpdateCheckCase):
    def test_a_newer_pypi_release_is_reported(self):
        found = self.check(current="0.9.1", latest="0.10.0")
        self.assertEqual(found, update_check.UpdateAvailable(current="0.9.1", latest="0.10.0"))

    def test_matching_versions_stay_silent(self):
        self.assertIsNone(self.check(current="0.9.1", latest="0.9.1"))

    def test_an_older_pypi_release_stays_silent(self):
        self.assertIsNone(self.check(current="0.9.1", latest="0.8.0"))

    def test_minor_version_ten_is_newer_than_nine(self):
        """0.10.0 must not sort as older than 0.9.1 (string compare would)."""
        self.assertIsNotNone(self.check(current="0.9.1", latest="0.10.0"))

    def test_a_fresh_cache_does_not_refetch(self):
        calls: list = []
        self.seed(checked_at=1_000_000.0, latest="0.10.0")
        found = update_check.check(
            DIST, "0.9.1", cache_path=self.cache, ttl_seconds=86_400, now=1_000_060.0,
            fetch=lambda dist, timeout: calls.append((dist, timeout)) or _pypi("9.9.9"))
        self.assertEqual(found.latest, "0.10.0")
        self.assertEqual(calls, [])

    def test_a_stale_cache_refetches(self):
        calls: list = []
        self.seed(checked_at=1.0, latest="0.9.2")
        found = update_check.check(
            DIST, "0.9.1", cache_path=self.cache, ttl_seconds=86_400, now=86_402.0,
            fetch=lambda dist, timeout: calls.append((dist, timeout)) or _pypi("0.10.0"))
        self.assertEqual(found.latest, "0.10.0")
        self.assertEqual(calls, [(DIST, update_check.DEFAULT_TIMEOUT)])

    def test_a_future_or_non_finite_stamp_is_stale(self):
        for stamp in (2_000_000.0, float("inf"), float("nan"), 10 ** 400):
            calls: list = []
            self.cache.write_text(json.dumps({"checked_at": stamp, "latest": "0.9.2"}),
                                  encoding="utf-8")
            found = self.check(calls=calls)
            self.assertEqual((found.latest, len(calls)), ("0.10.0", 1), stamp)

    def test_a_failed_fetch_stays_silent(self):
        self.assertIsNone(self.check(fetch=_offline))

    def test_malformed_json_stays_silent(self):
        self.assertIsNone(self.check(fetch=lambda *_a: "not-json"))

    def test_an_unparseable_current_version_stays_silent(self):
        self.assertIsNone(self.check(current="0+unknown", latest="0.10.0"))

    def test_a_failed_fetch_reuses_stale_latest(self):
        self.seed(checked_at=1.0, latest="0.10.0")
        found = update_check.check(DIST, "0.9.1", cache_path=self.cache, ttl_seconds=86_400,
                                   now=86_402.0, fetch=_offline)
        self.assertEqual(found.latest, "0.10.0")

    def test_a_failed_fetch_is_not_retried_until_the_ttl(self):
        calls: list = []

        def boom(_d: str, _t: float) -> str:
            calls.append(1)
            raise OSError("offline")

        self.check(fetch=boom, now=1_000.0, ttl=600)
        self.check(fetch=boom, now=1_599.0, ttl=600)
        self.assertEqual(calls, [1])
        self.check(fetch=boom, now=1_600.0, ttl=600)
        self.assertEqual(calls, [1, 1])

    def test_a_github_release_tag_is_read(self):
        found = self.check(fetch=lambda *_a: json.dumps({"tag_name": "v0.10.0"}))
        self.assertEqual(found, update_check.UpdateAvailable(current="0.9.1", latest="v0.10.0"))

    def test_a_v_prefixed_current_version_compares_numerically(self):
        self.assertEqual(self.check(current="v0.9.1", latest="0.10.0").latest, "0.10.0")

    def test_defaults(self):
        self.assertEqual(update_check.DEFAULT_TTL, 600)
        self.assertEqual(update_check.DEFAULT_TIMEOUT, 0.8)
        # several releases can land in one day: the next one is seen soon, not tomorrow
        self.assertLessEqual(update_check.DEFAULT_TTL, 3600)


class FetchTest(unittest.TestCase):
    def test_fetch_github_asks_for_the_latest_release(self):
        seen: list = []

        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

            def read(self, n=-1):
                return b'{"tag_name": "v1.2.0"}'

        class _Opener:
            def open(self, request, timeout):
                seen.append((request.full_url, timeout))
                return _Response()

        with mock.patch("urllib.request.build_opener", lambda *_h: _Opener()):
            body = update_check.fetch_github("owner/repo", 0.5)
        self.assertEqual(json.loads(body)["tag_name"], "v1.2.0")
        self.assertEqual(seen, [("https://api.github.com/repos/owner/repo/releases/latest", 0.5)])

    def test_fetch_pypi_asks_for_the_dist_json(self):
        seen: list = []

        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

            def read(self, n=-1):
                return b"{}"

        class _Opener:
            def open(self, request, timeout):
                seen.append((request.full_url, timeout))
                return _Response()

        with mock.patch("urllib.request.build_opener", lambda *_h: _Opener()):
            update_check.fetch_pypi(DIST, 0.5)
        self.assertEqual(seen, [(f"https://pypi.org/pypi/{DIST}/json", 0.5)])


class SecurityTest(UpdateCheckCase):
    EVIL = "9.9\x1b]52;c;x\x07"

    def test_a_non_version_from_the_body_or_cache_is_no_offer(self):
        self.assertIsNone(self.check(latest=self.EVIL))
        self.assertIsNone(update_check._latest_from_body(json.dumps({"tag_name": self.EVIL})))
        self.seed(latest=self.EVIL, checked_at=1_000_000.0)
        self.assertIsNone(self.check(now=1_000_001.0, fetch=_offline))

    def test_the_prompt_prints_no_escape_for_a_hostile_version(self):
        import io
        from contextlib import redirect_stderr

        from agent_toggle import update_prompt
        buf = io.StringIO()
        with redirect_stderr(buf):
            update_prompt.hint(update_check.UpdateAvailable(current="1.0", latest=self.EVIL))
        self.assertNotIn("\x1b", buf.getvalue())

    def test_a_deeply_nested_cache_is_corrupt_and_rewritten(self):
        depth = 300_000
        self.cache.write_text('{"a":' + "[" * depth + "]" * depth + "}", encoding="utf-8")
        found = self.check(latest="9.9.9")
        self.assertEqual(found, update_check.UpdateAvailable(current="0.9.1", latest="9.9.9"))
        self.assertEqual(json.loads(self.cache.read_text(encoding="utf-8"))["latest"], "9.9.9")

    def test_a_deeply_nested_dict_cache_is_dropped_and_rewritten_small(self):
        depth = 100_000        # parses on 3.14; json.dumps of the merged data used to raise
        self.cache.write_text('{"a":' * depth + "1" + "}" * depth, encoding="utf-8")
        found = self.check(latest="9.9.9")
        self.assertEqual(found, update_check.UpdateAvailable(current="0.9.1", latest="9.9.9"))
        text = self.cache.read_text(encoding="utf-8")
        self.assertLess(len(text), 200)
        self.assertEqual(set(json.loads(text)), {"checked_at", "latest"})

    def test_only_known_scalar_keys_survive_a_cache_load(self):
        self.seed(checked_at=5, latest="1.2.3", skipped="evil\x1b[2J", extra={"a": [1]})
        self.assertEqual(update_check._load_cache(self.cache), {"checked_at": 5, "latest": "1.2.3"})
        self.seed(checked_at=True, latest=["1.2.3"])
        self.assertEqual(update_check._load_cache(self.cache), {})

    def test_the_cache_write_replaces_a_symlink_and_is_0600(self):
        target = self.tmp / "victim"
        target.write_text("keep", encoding="utf-8")
        self.cache.symlink_to(target)
        self.check()
        self.assertFalse(self.cache.is_symlink())
        self.assertEqual(target.read_text(encoding="utf-8"), "keep")
        self.assertEqual(self.cache.stat().st_mode & 0o777, 0o600)

    def test_fetch_reads_at_most_a_megabyte(self):
        seen: list = []

        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

            def read(self, n=-1):
                seen.append(n)
                return b"{}"

        class _Opener:
            def open(self, request, timeout):
                return _Response()

        with mock.patch("urllib.request.build_opener", lambda *_h: _Opener()):
            update_check.fetch_pypi(DIST, 0.5)
        self.assertEqual(seen, [1_000_000])

    def test_redirects_to_non_https_are_refused(self):
        import urllib.error
        import urllib.request
        handlers: list = []
        with mock.patch("urllib.request.build_opener",
                        lambda *h: handlers.extend(h) or mock.MagicMock()):
            update_check.fetch_pypi(DIST, 0.5)
        req = urllib.request.Request("https://pypi.org/x")
        handler = handlers[0]()
        with self.assertRaises(urllib.error.URLError):
            handler.redirect_request(req, None, 302, "Found", {}, "http://evil.example/")

    def test_numeric_tuple_accepts_only_short_ascii_digits(self):
        self.assertIsNone(update_check._numeric_tuple("1." + "9" * 7))
        self.assertIsNone(update_check._numeric_tuple("1.\u0662"))   # Arabic-Indic digit
        self.assertEqual(update_check._numeric_tuple("1.2"), (1, 2))


class ImportsTest(unittest.TestCase):
    def test_the_checker_imports_only_the_stdlib(self):
        """The file is meant to be copied; it must not import agent_toggle."""
        tree = ast.parse(Path(update_check.__file__).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".", 1)[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
        self.assertNotIn("agent_toggle", imported)
        stdlib = {"__future__", "json", "subprocess", "threading", "time", "urllib",
                  "collections", "dataclasses", "pathlib", "os", "re"}
        self.assertLessEqual(imported, stdlib)


class StartCollectTest(UpdateCheckCase):
    def start(self, fetch):
        return update_check.start(DIST, "0.9.1", cache_path=self.cache, fetch=fetch)

    def test_start_and_collect_finds_an_update(self):
        found = update_check.collect(self.start(lambda *_a: _pypi("0.10.0")))
        self.assertEqual(found.latest, "0.10.0")

    def test_start_and_collect_stays_silent_when_current(self):
        self.assertIsNone(update_check.collect(self.start(lambda *_a: _pypi("0.9.1"))))

    def test_start_overlaps_with_the_callers_own_work(self):
        release = threading.Event()

        def slow(_d: str, _t: float) -> str:
            release.wait(timeout=1)
            return _pypi("0.10.0")

        started = self.start(slow)
        time.sleep(0.05)
        self.assertTrue(started.thread.is_alive())
        release.set()
        self.assertEqual(update_check.collect(started).latest, "0.10.0")

    def test_collect_with_no_started_check_is_silent(self):
        self.assertIsNone(update_check.collect(None))


class SkipTest(UpdateCheckCase):
    def test_a_skipped_version_stays_silent(self):
        update_check.skip(self.cache, "0.10.0")
        self.assertIsNone(self.check(latest="0.10.0"))

    def test_a_release_newer_than_the_skipped_one_is_reported(self):
        update_check.skip(self.cache, "0.10.0")
        self.assertEqual(self.check(latest="0.11.0").latest, "0.11.0")

    def test_a_refetch_keeps_the_skipped_version(self):
        update_check.skip(self.cache, "0.10.0")
        self.check(latest="0.10.0")
        self.assertEqual(json.loads(self.cache.read_text(encoding="utf-8"))["skipped"], "0.10.0")


class OfferTest(UpdateCheckCase):
    def offer(self, answer, *, latest="0.10.0", run=None, upgrade=None):
        asked: list = []

        def ask(found):
            asked.append(found)
            if isinstance(answer, BaseException):
                raise answer
            return answer

        started = update_check.start(DIST, "0.9.1", cache_path=self.cache,
                                     fetch=lambda *_a: _pypi(latest))
        result = update_check.offer(
            started, ask, cache_path=self.cache,
            upgrade=upgrade or ["uv", "tool", "upgrade", DIST],
            run=run or (lambda *_a, **_k: self.fail("upgrade must not run")))
        return result, asked

    def test_offer_does_not_ask_when_nothing_is_newer(self):
        result, asked = self.offer(update_check.UPDATE_NOW, latest="0.9.1")
        self.assertIsNone(result)
        self.assertEqual(asked, [])

    def test_offer_asks_with_the_found_release(self):
        _result, asked = self.offer(update_check.SKIP)
        self.assertEqual(asked, [update_check.UpdateAvailable(current="0.9.1", latest="0.10.0")])

    def test_offer_update_now_runs_the_upgrade_command(self):
        ran: list = []
        result, _ = self.offer(update_check.UPDATE_NOW,
                               run=lambda cmd, **_k: ran.append(cmd) or _Done(0))
        self.assertEqual(result, update_check.UPDATE_NOW)
        self.assertEqual(ran, [["uv", "tool", "upgrade", DIST]])

    def test_offer_reports_an_upgrade_that_exits_nonzero(self):
        result, _ = self.offer(update_check.UPDATE_NOW, run=lambda *_a, **_k: _Done(2))
        self.assertEqual(result, update_check.UPGRADE_FAILED)

    def test_offer_reports_a_missing_upgrade_tool(self):
        def missing(*_a, **_k):
            raise FileNotFoundError("uv")

        result, _ = self.offer(update_check.UPDATE_NOW, run=missing)
        self.assertEqual(result, update_check.UPGRADE_FAILED)

    def test_offer_skip_leaves_the_next_run_asking(self):
        result, _ = self.offer(update_check.SKIP)
        self.assertEqual(result, update_check.SKIP)
        self.assertIsNotNone(self.check(latest="0.10.0"))

    def test_offer_skip_version_silences_that_version(self):
        result, _ = self.offer(update_check.SKIP_VERSION)
        self.assertEqual(result, update_check.SKIP_VERSION)
        self.assertIsNone(self.check(latest="0.10.0"))

    def test_offer_never_raises_when_the_ui_does(self):
        result, _ = self.offer(RuntimeError("broken terminal"))
        self.assertIsNone(result)

    def test_offer_reads_ctrl_c_in_the_ui_as_skip(self):
        result, _ = self.offer(KeyboardInterrupt())
        self.assertEqual(result, update_check.SKIP)

    def steps(self, returncodes):
        ran: list = []
        codes = iter(returncodes)
        result, _ = self.offer(
            update_check.UPDATE_NOW,
            upgrade=lambda found: [["install", found.latest], ["reload"]],
            run=lambda cmd, **_k: ran.append(cmd) or _Done(next(codes)))
        return result, ran

    def test_offer_builds_upgrade_steps_from_the_found_release(self):
        result, ran = self.steps([0, 0])
        self.assertEqual(result, update_check.UPDATE_NOW)
        self.assertEqual(ran, [["install", "0.10.0"], ["reload"]])

    def test_offer_stops_at_the_first_failed_step(self):
        result, ran = self.steps([1])
        self.assertEqual(result, update_check.UPGRADE_FAILED)
        self.assertEqual(ran, [["install", "0.10.0"]])


if __name__ == "__main__":
    unittest.main()
