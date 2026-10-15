"""Shared offscreen Qt harness for GUI-touching unit tests.

Importing this module forces the Qt platform to ``offscreen`` so tests run
headless. Call :func:`ensure_app` to obtain a singleton ``QApplication``.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_app = None


def ensure_app():
    """Return a process-wide singleton QApplication (created on first call)."""
    global _app
    from PyQt6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])
    return _app
