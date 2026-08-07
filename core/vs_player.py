"""Frame-request driven playback for the in-process VapourSynth core.

Replaces the mpv JSON-IPC playback loop. The model is VapourSynth Editor's:
there is no player, only ``get_frame_async(n)`` requests against a filter graph
plus a paced clock deciding which ``n`` to ask for.

THREADING (the rule that shapes this whole module): VapourSynth runs the
completion callback on one of ITS OWN worker threads and reacquires the GIL
there (bundled binding source ``vapoursynth.pyx``: ``frameDoneCallback`` is
declared ``noexcept nogil`` and immediately does ``with gil:``). Touching a Qt
widget from that thread is undefined behaviour. So the callback here does only
pure-Python work — convert the frame to numpy and close it — and then emits a
Qt signal; PyQt6's AutoConnection turns that into a queued delivery on the GUI
thread, which is where the pixels finally reach the widget.

``get_frame`` / ``get_frame_async`` both release the GIL while VapourSynth
works (``with nogil: getFrame(...)``), so requests never stall the GUI thread.
Playback deliberately does NOT use ``clip.frames()``: that is a sequential
prefetching iterator (pyx defaults ``prefetch=num_threads``,
``backlog=prefetch*3``) and cannot serve seeks.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)


class FrameRequester(QObject):
    """Asynchronously pull BGR frames out of a VapourSynth clip.

    ``frame_ready(epoch, index, ndarray)`` and ``frame_failed(epoch, index,
    str)`` are always delivered on the receiver's thread (queued from the VS
    worker thread). ``epoch`` lets a superseded load discard late arrivals —
    the same generation-token pattern the mpv path needed for its slow async
    connect, still required here because building an lsmas ``.lwi`` index is
    slow and asynchronous.
    """

    frame_ready = pyqtSignal(int, int, object)
    frame_failed = pyqtSignal(int, int, str)

    #: Cap on simultaneously outstanding requests; a scrub must not queue
    #: hundreds of decodes that are already stale by the time they land.
    MAX_INFLIGHT = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self._clip: Optional[Any] = None
        self._epoch = 0
        self._lock = threading.Lock()
        self._inflight: set = set()
        self._latest_wanted: Optional[int] = None

    # ---- graph / lifecycle -------------------------------------------------

    def set_clip(self, clip: Optional[Any], epoch: int) -> None:
        """Point at a new graph; anything still in flight becomes stale."""
        with self._lock:
            self._clip = clip
            self._epoch = int(epoch)
            self._inflight.clear()
            self._latest_wanted = None

    @property
    def epoch(self) -> int:
        return self._epoch

    def has_clip(self) -> bool:
        return self._clip is not None

    def num_frames(self) -> int:
        clip = self._clip
        return int(getattr(clip, "num_frames", 0) or 0) if clip is not None else 0

    def clear(self) -> None:
        self.set_clip(None, self._epoch + 1)

    # ---- requests ----------------------------------------------------------

    def request(self, index: int, *, coalesce: bool = False) -> bool:
        """Ask for frame ``index``. Returns False when dropped.

        ``coalesce=True`` (scrubbing/playback) drops the request when the
        in-flight budget is full, remembering only the most recent target so
        the UI keeps up instead of falling behind a decode backlog.
        """
        with self._lock:
            clip = self._clip
            if clip is None:
                return False
            total = int(getattr(clip, "num_frames", 0) or 0)
            if total <= 0:
                return False
            index = max(0, min(int(index), total - 1))
            epoch = self._epoch
            if index in self._inflight:
                return False
            if len(self._inflight) >= self.MAX_INFLIGHT:
                if coalesce:
                    self._latest_wanted = index
                    return False
                return False
            self._inflight.add(index)

        def _done(future) -> None:
            # Runs on a VapourSynth worker thread: no Qt widget access here.
            arr = None
            error = ""
            try:
                frame = future.result()
                try:
                    from core.vs_frame import frame_to_bgr

                    arr = frame_to_bgr(frame)
                finally:
                    try:
                        frame.close()
                    except Exception:
                        pass
                if arr is None:
                    error = "unsupported frame format (expected planar RGB24)"
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"

            follow_up = None
            with self._lock:
                self._inflight.discard(index)
                if epoch == self._epoch and self._latest_wanted is not None:
                    follow_up = self._latest_wanted
                    self._latest_wanted = None

            if arr is not None:
                self.frame_ready.emit(epoch, index, arr)
            else:
                self.frame_failed.emit(epoch, index, error)
            if follow_up is not None and follow_up != index:
                self.request(follow_up, coalesce=True)

        try:
            future = clip.get_frame_async(index)
        except Exception as exc:
            with self._lock:
                self._inflight.discard(index)
            self.frame_failed.emit(epoch, index, f"{type(exc).__name__}: {exc}")
            return False

        try:
            future.add_done_callback(_done)
        except Exception:
            # Extremely old/odd future implementations: fall back to blocking.
            _done(future)
        return True

    def inflight_count(self) -> int:
        with self._lock:
            return len(self._inflight)
