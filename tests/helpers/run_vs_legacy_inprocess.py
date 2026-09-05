"""Clean-interpreter contracts for the retired in-process VS test coverage.

This is deliberately separate from ``run_vs_frame_probe.py``: these cases
exercise the legacy engine, graph and Qt requester contracts rather than the
frame-probe API.  Do not import PyQt before ``_load_vs``.
"""

from __future__ import annotations

import gc
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    import cv2
except ImportError:  # parent-side gate normally prevents this path
    cv2 = None


ROOT = Path(__file__).resolve().parents[2]


def _load_vs() -> tuple[Any, Any]:
    """Load the bundled core before any Qt-importing module is reached."""
    from core import vs_engine

    vs = vs_engine.load_vapoursynth()
    vs_engine.get_core()
    return vs, vs_engine


def _params(path: Path, **changes: Any) -> Any:
    from core.export_service import VideoExportParams

    fields = {
        "video_path": str(path),
        "cropbox": (0, 0, 0, 0),
        "start_frame": 0,
        "end_frame": 20,
        "fps": 30.0,
        "resolution": "360x640",
    }
    fields.update(changes)
    return VideoExportParams(**fields)


def _toolchain() -> Any:
    from core.media_tools import MediaToolchain

    return MediaToolchain.discover(str(ROOT))


def _write_source_mp4(directory: Path) -> Path:
    import numpy as np

    assert cv2 is not None
    image = np.zeros((360, 240, 3), np.uint8)
    image[:120, :] = (0, 0, 255)
    image[120:240, :80] = (0, 255, 0)
    image[240:, 160:] = (255, 0, 0)
    mp4 = directory / "source.mp4"
    writer = cv2.VideoWriter(
        str(mp4), cv2.VideoWriter_fourcc(*"mp4v"), 30, (240, 360)
    )
    assert writer.isOpened()
    try:
        for _ in range(40):
            writer.write(image)
    finally:
        writer.release()
    return mp4


def _vspipe_frame(directory: Path, params: Any, index: int) -> bytes:
    from core.media_tools import build_media_subprocess_env
    from core.vs_script import write_vpy_script

    toolchain = _toolchain()
    script = directory / f"parity-{index}.vpy"
    write_vpy_script(str(script), params)
    completed = subprocess.run(
        [toolchain.vspipe_path, "-c", "y4m", "-s", str(index), "-e", str(index), str(script), "-"],
        capture_output=True,
        env=build_media_subprocess_env(toolchain.vspipe_path),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-400:]
    body = completed.stdout.split(b"FRAME\n", 1)[1]
    for leftover in (script, script.with_suffix(".lwi")):
        leftover.unlink(missing_ok=True)
    return body


def _graph_frame(params: Any, index: int) -> bytes:
    import numpy as np

    from core.vs_graph import build_export_graph

    clip = build_export_graph(params)
    frame = None
    try:
        frame = clip.get_frame(index)
        return b"".join(
            np.ascontiguousarray(np.asarray(frame[plane])).tobytes()
            for plane in range(len(frame))
        )
    finally:
        if frame is not None:
            frame.close()
        del frame
        del clip


def _engine_contract() -> dict[str, object]:
    import ctypes

    from config.vsconfig import VSConfig, load_vsconfig

    vs, vs_engine = _load_vs()
    assert hasattr(vs, "core")
    assert str(vs_engine.vs_dir()) not in sys.path

    class Struct(ctypes.Structure):
        _fields_ = [("value", ctypes.c_int)]

    assert ctypes.sizeof(Struct) == 4
    assert vs_engine.load_vapoursynth() is vs_engine.load_vapoursynth()
    assert vs_engine.get_core() is vs_engine.get_core()
    for name in load_vsconfig().required_plugins:
        assert name in vs_engine.available_plugins(), name
    assert vs_engine.missing_plugins() == ()
    vs_engine.verify_plugins()
    missing = VSConfig(required_plugins=("lsmas", "imwri", "definitely_absent_ns"))
    assert vs_engine.missing_plugins(missing) == ("definitely_absent_ns",)
    try:
        vs_engine.verify_plugins(missing)
    except vs_engine.VSUnavailable:
        pass
    else:
        raise AssertionError("missing plugin was accepted")
    core = vs_engine.get_core()
    assert core.num_threads > 0 and core.max_cache_size > 0
    one = vs_engine.lwi_cache_path(r"C:\media\clip.mp4")
    assert one == vs_engine.lwi_cache_path(r"C:\media\clip.mp4")
    assert one.endswith(".lwi")
    assert os.path.join("media", "clip") not in one
    assert one != vs_engine.lwi_cache_path(r"C:\media\other.mp4")
    assert Path(vs_engine.vs_dir()).name == "media"
    assert issubclass(vs_engine.VSUnavailable, RuntimeError)
    return {"status": "ok"}


def _graph_contract() -> dict[str, object]:
    vs, vs_engine = _load_vs()
    with tempfile.TemporaryDirectory() as temp_dir:
        export_graph = None
        display_graph = None
        try:
            directory = Path(temp_dir)
            source = _write_source_mp4(directory)
            plain = _params(source)
            for index in (0, 7):
                assert _graph_frame(plain, index) == _vspipe_frame(directory, plain, index)
            cropped = _params(
                source,
                cropbox=(10, 20, 120, 213),
                rotation=180,
                start_frame=3,
                end_frame=25,
            )
            assert _graph_frame(cropped, 2) == _vspipe_frame(directory, cropped, 2)
            from core.vs_graph import build_display_graph, build_export_graph

            export_graph = build_export_graph(plain)
            display_graph = build_display_graph(plain)
            assert (export_graph.width, export_graph.height) == (384, 640)
            assert display_graph.format.id == vs.RGB24
        finally:
            del display_graph
            del export_graph
            vs_engine.clear_caches()
            gc.collect()
    return {"status": "ok"}


def _pump(app: Any, predicate: Any, timeout_s: float = 10.0) -> bool:
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return False


def _frame_requester_contract() -> dict[str, object]:
    vs, vs_engine = _load_vs()
    from PyQt6.QtCore import QCoreApplication, QThread
    from core.vs_player import FrameRequester

    app = QCoreApplication.instance() or QCoreApplication([])

    def clip(length: int = 30) -> Any:
        return vs_engine.get_core().std.BlankClip(
            width=64, height=48, length=length, format=vs.RGB24, color=[255, 0, 0]
        )

    requester = FrameRequester()
    requester.set_clip(clip(), epoch=1)
    received: dict[str, Any] = {}
    requester.frame_ready.connect(
        lambda epoch, index, array: received.update(
            epoch=epoch, index=index, array=array, thread=QThread.currentThread()
        )
    )
    assert requester.request(5)
    assert _pump(app, lambda: "array" in received)
    assert received["index"] == 5 and received["epoch"] == 1
    assert received["array"].shape == (48, 64, 3)
    assert tuple(int(value) for value in received["array"][0, 0]) == (0, 0, 255)
    assert received["thread"] is app.thread()

    requester = FrameRequester()
    requester.set_clip(clip(10), epoch=2)
    seen: list[int] = []
    requester.frame_ready.connect(lambda _epoch, index, _array: seen.append(index))
    assert requester.request(999)
    assert _pump(app, lambda: bool(seen)) and seen == [9]

    requester = FrameRequester()
    requester.set_clip(clip(), epoch=3)
    requester.set_clip(clip(), epoch=4)
    epochs: list[int] = []
    requester.frame_ready.connect(lambda epoch, _index, _array: epochs.append(epoch))
    assert requester.request(1)
    assert _pump(app, lambda: bool(epochs)) and epochs == [4]

    requester = FrameRequester()
    requester.set_clip(clip(200), epoch=5)
    peak = 0
    for index in range(50):
        requester.request(index, coalesce=True)
        peak = max(peak, requester.inflight_count())
    assert peak <= FrameRequester.MAX_INFLIGHT
    assert _pump(app, lambda: requester.inflight_count() == 0, timeout_s=15.0)
    return {"status": "ok"}


def _vui_contract() -> dict[str, object]:
    _load_vs()
    assert cv2 is not None
    import numpy as np
    from core.media_pipeline import MediaEncoder
    from core import vs_engine
    from tests.helpers.m5_render_fixture import (
        build_default_render_session,
        preflight_encode_request,
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        clip = None
        frame = None
        try:
            directory = Path(temp_dir)
            png = directory / "tag.png"
            assert cv2.imwrite(str(png), np.full((640, 360, 3), (60, 120, 180), np.uint8))
            session = build_default_render_session(
                directory / "m5-session",
                source_path=png,
                source_kind="image",
                end_frame=12,
                crop=(0, 0, 360, 640),
            )
            output = directory / "tag.mp4"
            request, fps, vui = preflight_encode_request(session)
            MediaEncoder(_toolchain()).encode_vpy_to_mp4(
                request, str(output), fps, vui=vui
            )
            clip = vs_engine.source_clip(str(output))
            frame = clip.get_frame(0)
            props = dict(frame.props)
        finally:
            if frame is not None:
                frame.close()
            del frame
            del clip
            vs_engine.clear_caches()
            gc.collect()
    assert props.get("_Matrix") == 6
    assert props.get("_Primaries") == 6
    assert props.get("_Transfer") == 6
    assert props.get("_ColorRange") == 1
    return {"status": "ok"}


CASES = {
    "engine_contract": _engine_contract,
    "graph_contract": _graph_contract,
    "frame_requester_contract": _frame_requester_contract,
    "vui_contract": _vui_contract,
}


def _main() -> dict[str, object]:
    if len(sys.argv) != 2:
        raise ValueError("expected exactly one probe case")
    return CASES[sys.argv[1]]()


if __name__ == "__main__":
    try:
        payload = _main()
    except BaseException as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}))
        raise
    print(json.dumps(payload))
