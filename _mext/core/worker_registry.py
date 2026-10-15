"""Lifecycle registry for extension background workers."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)


class WorkerRegistry:
    """Track extension workers for best-effort shutdown."""

    def __init__(self) -> None:
        self._qthreads: list[Any] = []
        self._thread_pools: list[Any] = []
        self._threads: list[threading.Thread] = []

    def register_qthread(self, thread: Any) -> Any:
        """Register a QThread-like object and return it."""
        if thread not in self._qthreads:
            self._qthreads.append(thread)
        return thread

    def unregister_qthread(self, thread: Any) -> None:
        """Remove a QThread-like object if it is registered."""
        if thread in self._qthreads:
            self._qthreads.remove(thread)

    def register_thread_pool(self, pool: Any) -> Any:
        """Register a QThreadPool-like object and return it."""
        if pool not in self._thread_pools:
            self._thread_pools.append(pool)
        return pool

    def unregister_thread_pool(self, pool: Any) -> None:
        """Remove a QThreadPool-like object if it is registered."""
        if pool in self._thread_pools:
            self._thread_pools.remove(pool)

    def register_thread(self, thread: threading.Thread) -> threading.Thread:
        """Register a Python thread and return it."""
        if thread not in self._threads:
            self._threads.append(thread)
        return thread

    def unregister_thread(self, thread: threading.Thread) -> None:
        """Remove a Python thread if it is registered."""
        if thread in self._threads:
            self._threads.remove(thread)

    @property
    def active_count(self) -> int:
        """Return the number of currently tracked workers."""
        return len(self._qthreads) + len(self._thread_pools) + len(self._threads)

    def shutdown(self, timeout_ms: int = 2000) -> bool:
        """Request all registered workers to stop and wait briefly.

        The registry never raises worker exceptions. It returns ``False`` if
        a worker still appears active after the timeout.
        """
        deadline = time.monotonic() + max(timeout_ms, 0) / 1000
        ok = True

        for thread in list(self._qthreads):
            ok = self._request_qthread_stop(thread) and ok

        for thread in list(self._qthreads):
            ok = self._wait_for_qthread(thread, deadline) and ok

        for pool in list(self._thread_pools):
            ok = self._wait_for_thread_pool(pool, deadline) and ok

        for thread in list(self._threads):
            ok = self._join_thread(thread, deadline) and ok

        return ok

    def _request_qthread_stop(self, thread: Any) -> bool:
        try:
            if hasattr(thread, "requestInterruption"):
                thread.requestInterruption()
            if hasattr(thread, "quit"):
                thread.quit()
            return True
        except Exception:
            logger.exception("Failed to request extension QThread stop")
            return False

    def _wait_for_qthread(self, thread: Any, deadline: float) -> bool:
        try:
            is_running = getattr(thread, "isRunning", None)
            if callable(is_running) and not is_running():
                self.unregister_qthread(thread)
                return True

            wait = getattr(thread, "wait", None)
            if callable(wait):
                wait(_remaining_ms(deadline))

            if callable(is_running) and is_running():
                return False

            self.unregister_qthread(thread)
            return True
        except Exception:
            logger.exception("Failed while waiting for extension QThread")
            return False

    def _wait_for_thread_pool(self, pool: Any, deadline: float) -> bool:
        try:
            clear = getattr(pool, "clear", None)
            if callable(clear):
                clear()

            wait_for_done = getattr(pool, "waitForDone", None)
            if callable(wait_for_done):
                result = wait_for_done(_remaining_ms(deadline))
                if result is False:
                    return False

            self.unregister_thread_pool(pool)
            return True
        except Exception:
            logger.exception("Failed while waiting for extension QThreadPool")
            return False

    def _join_thread(self, thread: threading.Thread, deadline: float) -> bool:
        try:
            if thread is threading.current_thread():
                return False

            if thread.is_alive():
                thread.join(_remaining_ms(deadline) / 1000)

            if thread.is_alive():
                return False

            self.unregister_thread(thread)
            return True
        except Exception:
            logger.exception("Failed while joining extension thread")
            return False


def _remaining_ms(deadline: float) -> int:
    return max(0, int((deadline - time.monotonic()) * 1000))
