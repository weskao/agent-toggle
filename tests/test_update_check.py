"""Update hint on top of update_kit.check.

Never hits the network: every test mocks ``update_kit.check`` (which returns the newer
release tag, or None) and injects ``run``. The lookup, its TTL and its cache are
update-kit's; what these cover is the skip-version memory, the background start/collect
and the offer answers.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import update_kit

from agent_toggle import update_check


class _Done:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


class UpdateCheckCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="agent-toggle-update-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cache = self.tmp / "update-check.json"

    def kit(self, latest="v0.10.0", **kw):
        """Patch update_kit.check to answer *latest* (or raise via side_effect=...)."""
        patch = mock.patch.object(update_kit, "check", return_value=latest, **kw)
        self.addCleanup(patch.stop)
        return patch.start()

    def check(self, latest="v0.10.0", current="0.9.1", **kw):
        self.kit(latest, **kw)
        return update_check.check(current, cache_path=self.cache)

    def seed(self, **data) -> None:
        self.cache.write_text(json.dumps(data), encoding="utf-8")


class CheckTest(UpdateCheckCase):
    def test_a_newer_tag_is_reported_as_update_kit_returns_it(self):
        found = self.check("v0.10.0")
        self.assertEqual(found, update_check.UpdateAvailable(current="0.9.1", latest="v0.10.0"))

    def test_the_lookup_is_update_kits_for_this_repo(self):
        kit = self.kit()
        update_check.check("0.9.1", cache_path=self.cache)
        kit.assert_called_once_with(repo="weskao/agent-toggle", current="0.9.1")

    def test_nothing_newer_stays_silent(self):
        self.assertIsNone(self.check(None))

    def test_a_failing_lookup_stays_silent(self):
        self.assertIsNone(self.check(side_effect=OSError("offline")))

    def test_a_missing_update_kit_stays_silent(self):
        with mock.patch.dict("sys.modules", {"update_kit": None}):
            self.assertIsNone(update_check.check("0.9.1", cache_path=self.cache))


class StartCollectTest(UpdateCheckCase):
    def start(self, latest="v0.10.0", **kw):
        self.kit(latest, **kw)
        return update_check.start("0.9.1", cache_path=self.cache)

    def test_start_and_collect_finds_an_update(self):
        self.assertEqual(update_check.collect(self.start()).latest, "v0.10.0")

    def test_start_and_collect_stays_silent_when_current(self):
        self.assertIsNone(update_check.collect(self.start(None)))

    def test_start_overlaps_with_the_callers_own_work(self):
        release = threading.Event()

        def slow(**_kw):
            release.wait(timeout=1)
            return "v0.10.0"

        started = self.start(side_effect=slow)
        time.sleep(0.05)
        self.assertTrue(started.thread.is_alive())
        release.set()
        self.assertEqual(update_check.collect(started).latest, "v0.10.0")

    def test_collect_with_no_started_check_is_silent(self):
        self.assertIsNone(update_check.collect(None))


class SkipTest(UpdateCheckCase):
    def test_a_skipped_version_stays_silent(self):
        update_check.skip(self.cache, "v0.10.0")
        self.assertIsNone(self.check("v0.10.0"))

    def test_a_skip_made_before_tags_still_matches(self):
        self.seed(skipped="0.10.0")        # what the PyPI-era check stored
        self.assertIsNone(self.check("v0.10.0"))

    def test_a_release_newer_than_the_skipped_one_is_reported(self):
        update_check.skip(self.cache, "v0.10.0")
        self.assertEqual(self.check("v0.11.0").latest, "v0.11.0")

    def test_skip_keeps_only_the_skipped_version(self):
        self.seed(checked_at=5, latest="0.9.2", extra={"a": [1]})
        update_check.skip(self.cache, "v0.10.0")
        self.assertEqual(json.loads(self.cache.read_text(encoding="utf-8")), {"skipped": "v0.10.0"})


class SecurityTest(UpdateCheckCase):
    def test_a_hostile_skipped_value_is_ignored(self):
        self.seed(skipped="evil\x1b[2J", latest=["1.2.3"])
        self.assertEqual(update_check._load_cache(self.cache), {})

    def test_the_prompt_prints_no_escape_for_a_hostile_version(self):
        import io
        from contextlib import redirect_stderr

        from agent_toggle import update_prompt
        buf = io.StringIO()
        with redirect_stderr(buf):
            update_prompt.hint(update_check.UpdateAvailable(current="1.0", latest="9.9\x1b]52;c;x\x07"))
        self.assertNotIn("\x1b", buf.getvalue())

    def test_the_prompt_shows_the_tag_without_its_v(self):
        from agent_toggle import update_prompt
        text = update_prompt._available(update_check.UpdateAvailable(current="1.0", latest="v2.0.0"))
        self.assertEqual(text, "agent-toggle 2.0.0 is available (you have 1.0)")

    def test_a_deeply_nested_cache_is_corrupt_and_rewritten_small(self):
        depth = 100_000        # parses on 3.14; json.dumps of the merged data used to raise
        self.cache.write_text('{"a":' * depth + "1" + "}" * depth, encoding="utf-8")
        update_check.skip(self.cache, "v0.10.0")
        self.assertEqual(json.loads(self.cache.read_text(encoding="utf-8")), {"skipped": "v0.10.0"})

    def test_the_cache_write_replaces_a_symlink_and_is_0600(self):
        target = self.tmp / "victim"
        target.write_text("keep", encoding="utf-8")
        self.cache.symlink_to(target)
        update_check.skip(self.cache, "v0.10.0")
        self.assertFalse(self.cache.is_symlink())
        self.assertEqual(target.read_text(encoding="utf-8"), "keep")
        if os.name != "nt":                      # Windows has no POSIX modes
            self.assertEqual(self.cache.stat().st_mode & 0o777, 0o600)


class OfferTest(UpdateCheckCase):
    def offer(self, answer, *, latest="v0.10.0", run=None, upgrade=None):
        asked: list = []

        def ask(found):
            asked.append(found)
            if isinstance(answer, BaseException):
                raise answer
            return answer

        self.kit(latest)
        started = update_check.start("0.9.1", cache_path=self.cache)
        result = update_check.offer(
            started, ask, cache_path=self.cache,
            upgrade=upgrade or ["uv", "tool", "upgrade", "agent-toggle"],
            run=run or (lambda *_a, **_k: self.fail("upgrade must not run")))
        return result, asked

    def test_offer_does_not_ask_when_nothing_is_newer(self):
        result, asked = self.offer(update_check.UPDATE_NOW, latest=None)
        self.assertIsNone(result)
        self.assertEqual(asked, [])

    def test_offer_asks_with_the_found_release(self):
        _result, asked = self.offer(update_check.SKIP)
        self.assertEqual(asked, [update_check.UpdateAvailable(current="0.9.1", latest="v0.10.0")])

    def test_offer_update_now_runs_the_upgrade_command(self):
        ran: list = []
        result, _ = self.offer(update_check.UPDATE_NOW,
                               run=lambda cmd, **_k: ran.append(cmd) or _Done(0))
        self.assertEqual(result, update_check.UPDATE_NOW)
        self.assertEqual(ran, [["uv", "tool", "upgrade", "agent-toggle"]])

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
        self.assertIsNotNone(self.check("v0.10.0"))

    def test_offer_skip_version_silences_that_version_until_a_newer_one(self):
        result, _ = self.offer(update_check.SKIP_VERSION)
        self.assertEqual(result, update_check.SKIP_VERSION)
        self.assertIsNone(self.check("v0.10.0"))
        self.assertEqual(self.check("v0.11.0").latest, "v0.11.0")

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
        self.assertEqual(ran, [["install", "v0.10.0"], ["reload"]])

    def test_offer_stops_at_the_first_failed_step(self):
        result, ran = self.steps([1])
        self.assertEqual(result, update_check.UPGRADE_FAILED)
        self.assertEqual(ran, [["install", "v0.10.0"]])


if __name__ == "__main__":
    unittest.main()
