import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = [
    "main.py",
    "build.py",
    "pyproject.toml",
    "config",
    "core",
    "gui",
    "utils",
    "_mext",
    "tests",
]

MOJIBAKE_SIGNATURES = [
    "\ufffd",
    "\u951f\u65a4\u62f7",
    "\xef\xbc",
    "\xe2\u20ac\u2122",
    "\xe2\u20ac\u0153",
    "\xe2\u20ac\ufffd",
    "\u93c4",
    "\u95ab",
    "\u93b5",
    "\u6748",
    "\u95c2",
    "\u95bf",
    "\u7ecb",
    "\u7459",
    "\u9359",
    "\u5bf0\ue046\u5e46",
]


def _iter_source_files():
    this_file = Path(__file__).resolve()
    for source_root in SOURCE_ROOTS:
        path = REPO_ROOT / source_root
        if path.is_file():
            if path == this_file:
                continue
            yield path
            continue

        for child in path.rglob("*"):
            if child == this_file:
                continue
            if child.suffix in {".py", ".toml"} and "__pycache__" not in child.parts:
                yield child


class SourceEncodingTests(unittest.TestCase):
    def test_source_files_are_utf8_without_common_mojibake(self):
        failures = []
        for path in _iter_source_files():
            rel_path = path.relative_to(REPO_ROOT)
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                failures.append(f"{rel_path}: invalid UTF-8 ({exc})")
                continue

            for signature in MOJIBAKE_SIGNATURES:
                if signature in text:
                    failures.append(f"{rel_path}: contains {signature!r}")

        self.assertEqual([], failures)


if __name__ == "__main__":
    unittest.main()
