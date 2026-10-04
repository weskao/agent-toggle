"""The zh-TW catalogue must stay complete -- checked against the source, not a list.

Every ``t("msgid", "English", ...)`` call in ``agent_toggle/**/*.py`` is
re-derived with :mod:`ast` on each run, so the test grows itself. Failures:
a msgid with no zh-TW entry, a catalogue entry nothing uses, a ``%s``/``%d``
placeholder mismatch, one msgid with two English defaults, and one msgid in
two area files. Only literal first arguments are seen: pass the msgid as a
plain string at the call site.
"""
from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections import Counter, defaultdict
from pathlib import Path
from unittest import mock

from agent_toggle import i18n
from agent_toggle.locales import zh_tw_cli, zh_tw_common, zh_tw_config, zh_tw_picker

PKG = Path(i18n.__file__).resolve().parent
AREAS = {"zh_tw_cli": zh_tw_cli, "zh_tw_picker": zh_tw_picker,
         "zh_tw_config": zh_tw_config, "zh_tw_common": zh_tw_common}
PLACEHOLDER = re.compile(r"%[sd]")


def collect_source(text: str) -> list[tuple[str, str | None, int]]:
    """``(msgid, english default, lineno)`` for each t()/i18n.t() call with a literal msgid."""
    found = []
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        first = node.args[0]
        if name != "t" or not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            continue
        english = None
        if len(node.args) > 1:
            second = node.args[1]
            if isinstance(second, ast.Constant) and isinstance(second.value, str):
                english = second.value
        found.append((first.value, english, node.lineno))
    return found


def collect() -> tuple[dict[str, set[str]], dict[str, str]]:
    englishes: dict[str, set[str]] = defaultdict(set)
    origin: dict[str, str] = {}
    for path in sorted(PKG.rglob("*.py")):
        if path.name == "i18n.py":
            continue
        for msgid, english, lineno in collect_source(path.read_text(encoding="utf-8")):
            origin.setdefault(msgid, f"{path.relative_to(PKG.parent)}:{lineno}")
            bucket = englishes[msgid]
            if english is not None:
                bucket.add(english)
    return dict(englishes), origin


MSGIDS, ORIGIN = collect()


def placeholders(text: str) -> list[str]:
    return sorted(PLACEHOLDER.findall(text.replace("%%", "")))


class CatalogTest(unittest.TestCase):
    def test_the_collector_sees_calls_it_claims_to(self):
        """A collector that finds nothing would make the tests below pass vacuously."""
        src = ('t("a_b", "x %s")\ni18n.t("c_d",\n    "y")\nt(var, "z")\nother("e_f", "w")\n'
               'print(f"{t(\'g_h\', \'v\')}")\n')
        self.assertEqual([(m, e) for m, e, _ in collect_source(src)],
                         [("a_b", "x %s"), ("c_d", "y"), ("g_h", "v")])

    def test_every_msgid_used_has_a_zh_tw_translation(self):
        missing = sorted(set(MSGIDS) - set(i18n.CATALOG))
        self.assertFalse(missing, "no zh-TW entry in locales/ for: " + ", ".join(
            f"{m} ({ORIGIN[m]})" for m in missing))

    def test_the_catalogue_has_no_entries_nothing_uses(self):
        orphans = sorted(set(i18n.CATALOG) - set(MSGIDS))
        self.assertFalse(orphans, f"translated but never used: {orphans}")

    def test_no_msgid_carries_two_different_english_defaults(self):
        forked = {m: sorted(e) for m, e in MSGIDS.items() if len(e) > 1}
        self.assertFalse(forked, f"same msgid, different English: {forked}")

    def test_every_translation_takes_the_same_placeholders_as_its_english(self):
        wrong = {m: (e, i18n.CATALOG[m]) for m, es in MSGIDS.items() for e in es
                 if m in i18n.CATALOG and placeholders(e) != placeholders(i18n.CATALOG[m])}
        self.assertFalse(wrong, f"%s/%d placeholders differ between English and zh-TW: {wrong}")

    def test_no_msgid_is_in_two_area_files(self):
        counts = Counter(m for mod in AREAS.values() for m in mod.CATALOG)
        self.assertEqual({m: n for m, n in counts.items() if n > 1}, {})

    def test_merge_raises_on_a_duplicate_across_areas(self):
        with self.assertRaisesRegex(RuntimeError, "'dup_id'.*zh_tw_cli.*zh_tw_picker"):
            i18n._merge({"zh_tw_cli": {"dup_id": "a"}, "zh_tw_picker": {"dup_id": "b"}})
        self.assertEqual(i18n._merge({"a": {"x_y": "1"}, "b": {"z_w": "2"}}),
                         {"x_y": "1", "z_w": "2"})

    def test_the_area_modules_are_what_i18n_merges(self):
        self.assertEqual(i18n.CATALOG, {m: s for mod in AREAS.values() for m, s in mod.CATALOG.items()})


class MechanismTest(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(setattr, i18n, "LANGUAGE", i18n.LANGUAGE)

    def test_english_is_returned_verbatim(self):
        i18n.set_language("en")
        self.assertEqual(i18n.t("no_such_id", "100% verbatim"), "100% verbatim")
        self.assertEqual(i18n.t("no_such_id", "got %s", 3), "got 3")

    def test_zh_tw_uses_the_catalogue_and_falls_back_to_english(self):
        with mock.patch.dict(i18n.CATALOG, {"demo_id": "已處理 %s 個 (100%%)"}):
            i18n.set_language("zh-TW")
            self.assertEqual(i18n.t("demo_id", "Done %s (100%%)", 2), "已處理 2 個 (100%)")
            self.assertEqual(i18n.t("untranslated_id", "English only"), "English only")
            i18n.set_language("en")
            self.assertEqual(i18n.t("demo_id", "Done %s (100%%)", 2), "Done 2 (100%)")

    def test_set_language_validates(self):
        self.assertEqual(i18n.set_language("zh-TW"), "zh-TW")
        self.assertEqual(i18n.LANGUAGE, "zh-TW")
        self.assertEqual(i18n.set_language("fr"), "en")
        self.assertEqual(i18n.LANGUAGE, "en")

    def _lang_in_subprocess(self, value: str | None):
        env = {k: v for k, v in os.environ.items() if k != "AGENT_TOGGLE_LANG"}
        if value is not None:
            env["AGENT_TOGGLE_LANG"] = value
        return subprocess.run(
            [sys.executable, "-c", "import agent_toggle.i18n as i;print(i.LANGUAGE)"],
            capture_output=True, text=True, encoding="utf-8", env=env, check=False,
            cwd=str(PKG.parent))

    def test_the_initial_language_comes_from_the_environment(self):
        self.assertEqual(self._lang_in_subprocess(None).stdout.strip(), "en")
        self.assertEqual(self._lang_in_subprocess("zh-TW").stdout.strip(), "zh-TW")

    def test_an_unknown_env_language_falls_back_to_english_and_any_case_is_accepted(self):
        out = self._lang_in_subprocess("fr")
        self.assertEqual(out.stdout.strip(), "en")
        self.assertEqual(out.stderr, "")                # settings owns the one warning
        out = self._lang_in_subprocess("zh-tw")
        self.assertEqual((out.stdout.strip(), out.stderr), ("zh-TW", ""))

    def test_the_cli_warns_once_about_an_env_language_and_agrees_with_it(self):
        home = tempfile.mkdtemp(prefix="agent-toggle-i18n-")      # never the real config.json
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        env = {**os.environ, "AGENT_TOGGLE_UPDATE_CHECK": "0", "HOME": home, "USERPROFILE": home}
        for value, warnings, lang_word in (("fr", 1, "Usage"), ("zh-tw", 0, "用法")):
            out = subprocess.run(
                [sys.executable, "-m", "agent_toggle", "help"], capture_output=True, text=True,
                encoding="utf-8", env={**env, "AGENT_TOGGLE_LANG": value}, check=False,
                cwd=str(PKG.parent))
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(out.stderr.count("AGENT_TOGGLE_LANG"), warnings, out.stderr)
            self.assertIn(lang_word.upper(), out.stdout)


if __name__ == "__main__":
    unittest.main()
