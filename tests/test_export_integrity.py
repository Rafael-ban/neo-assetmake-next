"""Offscreen regression test for crash-recovery wiring (M2d)."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import unittest
from pathlib import Path

from tests.qt_harness import ensure_app


def setUpModule():
    ensure_app()


class CrashRecoveryWiringTests(unittest.TestCase):
    """M2d: an autosave backup must become a discoverable, recoverable pointer."""

    def test_autosave_pointer_roundtrip(self):
        from core.crash_recovery_service import CrashRecoveryService
        crs = CrashRecoveryService()
        base = Path(tempfile.mkdtemp())
        crs.initialize(str(base))
        backup = base / "autosave_x.json"
        backup.write_text('{"name": "proj"}', encoding="utf-8")

        # This is exactly what MainWindow._on_autosave_saved calls.
        crs.save_recovery_info(str(backup), project_path=None, is_temp=True)

        found = crs.check_crash_recovery()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].backup_path, str(backup))

        target = base / "restored.json"
        self.assertTrue(crs.recover_project(found[0], str(target)))
        self.assertTrue(target.exists())


if __name__ == "__main__":
    unittest.main()
