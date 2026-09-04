"""Legacy in-process VapourSynth engine contracts run outside the Qt parent."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from tests.helpers.vs_isolation import IsolatedVSCase


REPO = Path(__file__).resolve().parents[1]
VS_OK = (
    (REPO / "tools" / "media" / "vapoursynth.pyd").is_file()
    and sys.version_info >= (3, 12)
)


class ParentProcessIsolationTests(IsolatedVSCase):
    def test_parent_does_not_import_the_legacy_vs_engine(self):
        self.assert_parent_has_no_vs()


@unittest.skipUnless(
    VS_OK, "bundled VapourSynth (tools/media) or Python 3.12+ unavailable"
)
class VSEngineTests(IsolatedVSCase):
    def test_engine_loading_plugins_and_core_contract(self):
        self.assertEqual(self.run_vs_child("engine_contract")["status"], "ok")


if __name__ == "__main__":
    unittest.main()
