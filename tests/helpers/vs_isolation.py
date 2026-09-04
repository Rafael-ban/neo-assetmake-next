"""Parent-side runner for assertions that need an in-process VS core.

The runner intentionally has no VapourSynth or PyQt import.  Test modules may
therefore import it after their Qt harness without reviving the former
prewarm-before-Qt dependency.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[2]
CHILD_PROBE = ROOT / "tests" / "helpers" / "run_vs_legacy_inprocess.py"


class IsolatedVSCase(TestCase):
    """Run one legacy in-process VS contract in a clean child interpreter."""

    def assert_parent_has_no_vs(self) -> None:
        self.assertNotIn("vapoursynth", sys.modules)
        self.assertNotIn("core.vs_engine", sys.modules)

    def run_vs_child(self, case: str, *args: str) -> dict[str, Any]:
        self.assert_parent_has_no_vs()
        completed = subprocess.run(
            [sys.executable, str(CHILD_PROBE), case, *args],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
        detail = (
            f"VS child case {case!r} failed (exit {completed.returncode})\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
        self.assertEqual(completed.returncode, 0, detail)
        try:
            result = json.loads(completed.stdout.splitlines()[-1])
        except (IndexError, json.JSONDecodeError) as exc:
            self.fail(f"VS child {case!r} did not return JSON: {detail}\n{exc}")
        self.assert_parent_has_no_vs()
        return result
