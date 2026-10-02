"""Text I/O must name its encoding: Windows defaults to the locale code page (cp1252),
which cannot read the shim's zh-TW text or a non-ASCII config, while macOS/Linux hide it."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_MODES_ONLY = {"read_text", "write_text"}


def _kw(call: ast.Call, name: str) -> ast.expr | None:
    return next((k.value for k in call.keywords if k.arg == name), None)


def _is_binary(call: ast.Call) -> bool:
    mode = call.args[1] if len(call.args) > 1 else _kw(call, "mode")
    return isinstance(mode, ast.Constant) and "b" in str(mode.value)


def offenders(path: Path) -> list[str]:
    bad = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
        owner = f.value.id if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) else ""
        text_io = (
            (name in TEXT_MODES_ONLY and isinstance(f, ast.Attribute))
            or (name == "open" and owner != "os" and not _is_binary(node))
            or (name == "fdopen" and not _is_binary(node))
            or (name == "run" and owner == "subprocess"
                and isinstance(_kw(node, "text"), ast.Constant) and _kw(node, "text").value)
        )
        if text_io and _kw(node, "encoding") is None:
            bad.append(f"{path.relative_to(ROOT)}:{node.lineno} {name}()")
    return bad


class EncodingTest(unittest.TestCase):
    def test_every_text_io_call_names_its_encoding(self) -> None:
        files = [*(ROOT / "agent_toggle").rglob("*.py"), *(ROOT / "tests").glob("*.py")]
        bad = [o for p in sorted(files) if p.name != Path(__file__).name for o in offenders(p)]
        self.assertEqual(bad, [], "add encoding='utf-8':\n" + "\n".join(bad))


if __name__ == "__main__":
    unittest.main()
