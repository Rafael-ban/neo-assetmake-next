"""VapourSynth VideoFrame -> numpy conversion for in-process preview.

Three details here are load-bearing and each was verified empirically against
the bundled VapourSynth R73:

1. **Plane order.** VS RGB24 is PLANAR and plane 0 is R, 1 is G, 2 is B
   (verified: ``BlankClip(format=RGB24, color=[255,0,0])`` puts 255 in plane 0,
   and that clip converts to Y=63 under ``matrix_s='709'`` — the BT.709 luma of
   pure red). Everything in this app holds frames as **BGR** (``cv2.imdecode``,
   ``video_preview._to_rgb``, ``export_service._to_bgra_bytes_source``), so the
   planes must be stacked ``[2, 1, 0]``.
2. **Stride padding.** ``frame.get_stride(p)`` is not ``width * itemsize``
   (measured: stride 384 for width 360), so the rows must be sliced to width or
   the image shears.
3. **Lifetime.** ``frame[plane]`` is a memoryview over VS-owned memory
   (``video_view``); the array MUST be copied before ``frame.close()`` or it
   dangles.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)


def plane_to_array(frame: Any, plane: int) -> np.ndarray:
    """Copy one plane out of a VideoFrame as a 2-D numpy array (stride-safe)."""
    view = frame[plane]
    arr = np.asarray(view)
    if arr.ndim != 2:  # defensive: R73 exposes 2-D views
        arr = arr.reshape(frame.height, -1)
    # np.asarray already honours the view's strides; copy to detach from
    # VS-owned memory so the result survives frame.close().
    return np.ascontiguousarray(arr)


def frame_to_bgr(frame: Any) -> Optional[np.ndarray]:
    """Convert an RGB24 (planar) VideoFrame to a contiguous BGR uint8 array.

    Returns None when the frame is not 3-plane 8-bit RGB — callers should
    convert in the VS graph (``resize.<kernel>(clip, format=vs.RGB24)``) rather
    than guessing here.
    """
    try:
        planes = len(frame)
    except Exception:
        return None
    if planes != 3:
        return None
    try:
        r = plane_to_array(frame, 0)
        g = plane_to_array(frame, 1)
        b = plane_to_array(frame, 2)
    except Exception:
        logger.exception("VideoFrame plane read failed")
        return None
    if r.dtype != np.uint8:
        return None
    # VS planar RGB -> interleaved BGR (this app's in-memory convention).
    return np.ascontiguousarray(np.stack((b, g, r), axis=-1))


def request_bgr_frame(clip: Any, index: int) -> Optional[np.ndarray]:
    """Synchronously fetch frame ``index`` from ``clip`` as BGR numpy.

    Blocking, but ``get_frame`` releases the GIL (bundled binding source
    vapoursynth.pyx: ``with nogil: getFrame(...)``), so this is safe to call
    from a worker thread while the GUI thread keeps running. Do NOT call it on
    the GUI thread for anything but a trivial clip.
    """
    frame = clip.get_frame(int(index))
    try:
        return frame_to_bgr(frame)
    finally:
        try:
            frame.close()
        except Exception:
            pass


def to_display_rgb_clip(clip: Any, vs_module: Any, kernel: str = "Bicubic") -> Any:
    """Append the RGB24 conversion a preview needs to the end of a graph.

    The export graph ends at YUV420P8; converting THAT node back to RGB (rather
    than building a separate RGB chain) is what makes the preview show the
    exported pixels — "看到什么 = 导出什么".
    """
    from core import vs_engine

    core = vs_module.core
    if clip.format.id == vs_module.RGB24:
        return clip
    # Single authoritative lookup: a misspelt kernel must not silently become
    # Bicubic (that made a wrong config look like it was honoured).
    resizer = vs_engine.resize_filter(core, kernel)
    # matrix_in_s tells zimg how to interpret the YUV it is converting FROM.
    return resizer(clip, format=vs_module.RGB24, matrix_in_s="170m")
