"""Shared offscreen Qt harness for GUI-touching unit tests.

Importing this module forces the Qt platform to ``offscreen`` so tests run
headless. Call :func:`ensure_app` to obtain a singleton ``QApplication``.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Warm the VapourSynth core BEFORE any PyQt6 module loads, mirroring main.py.
# Initializing the VS core in a process that already imported PyQt6 segfaults
# on this bundle (verified for QtCore/QtGui/QtWidgets/QtNetwork, with and
# without a QApplication); the reverse order is clean. Importing this harness
# is the earliest shared hook the test suite has, so the ordering that ships in
# main.py is also the ordering the tests exercise.
try:
    from core.vs_engine import prewarm as _vs_prewarm

    _vs_prewarm()
except Exception:
    pass

_app = None


def ensure_app():
    """Return a process-wide singleton QApplication (created on first call)."""
    global _app
    from PyQt6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])
    return _app
