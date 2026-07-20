"""Regression test for the crash-recovery directory path (L3)."""
import os
import tempfile
import unittest

from core.crash_recovery_service import CrashRecoveryService


class CrashRecoveryPathTests(unittest.TestCase):
    def test_initialize_appends_a_single_recovery_dir(self):
        base = tempfile.mkdtemp()
        crs = CrashRecoveryService()
        crs.initialize(base)
        self.assertEqual(crs._recovery_dir, os.path.join(base, ".recovery"))
        self.assertEqual(crs._recovery_dir.count(".recovery"), 1)  # not doubled


if __name__ == "__main__":
    unittest.main()
