"""无 Qt、无父进程 in-process VS 污染的 M5 真实 runner 子进程。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _preview_export_contract() -> dict[str, object]:
    import cv2
    import numpy as np
    from PIL import Image

    from core.media_pipeline import MediaEncoder
    from core.media_tools import MediaToolchain
    from core.vs_runtime.worker_process import SyncVSWorkerProcess
    from tests.helpers.m5_render_fixture import (
        build_default_render_session,
        preflight_encode_request,
    )

    toolchain = MediaToolchain.discover(str(ROOT))
    if toolchain.missing_for_export():
        raise RuntimeError(f"M5 real encode unavailable: {toolchain.describe()}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir) / "中文 空格 & '单引号'"
        root.mkdir()
        source = root / "source.png"
        image = np.zeros((480, 320, 3), np.uint8)
        image[:240, :160] = (255, 0, 0)
        image[:240, 160:] = (0, 255, 0)
        image[240:, :160] = (0, 0, 255)
        image[240:, 160:] = (255, 255, 0)
        Image.fromarray(image[:, :, ::-1]).save(source)
        session = build_default_render_session(
            root / "session",
            source_path=source,
            source_kind="image",
            end_frame=4,
            crop=(20, 40, 180, 320),
            rotation=90,
        )
        worker = SyncVSWorkerProcess(
            app_dir=ROOT,
            env={**os.environ, "APPDATA": str(root / "appdata")},
        )
        try:
            worker.start(timeout_ms=15_000)
            worker.load(session, timeout_ms=30_000)
            preview = worker.request_frame(
                epoch=session.epoch,
                index=0,
                surface="final",
                viewport=(384, 640),
                zoom_factor=1.0,
                pan=(0.5, 0.5),
                timeout_ms=30_000,
            )
        finally:
            worker.close()
        request, fps, vui = preflight_encode_request(session)
        output = root / "encoded.mp4"
        MediaEncoder(toolchain).encode_vpy_to_mp4(request, str(output), fps, vui=vui)
        capture = cv2.VideoCapture(str(output))
        try:
            ok, encoded = capture.read()
        finally:
            capture.release()
        if not ok or encoded is None:
            raise RuntimeError("cannot decode fixed-runner MP4")
        if preview.shape != encoded.shape:
            raise AssertionError(f"geometry mismatch: {preview.shape} != {encoded.shape}")
        difference = np.abs(preview.astype(np.int16) - encoded.astype(np.int16))
        if difference.mean() >= 3.0:
            raise AssertionError(f"mean BGR difference too large: {difference.mean():.3f}")
        if (difference.max(axis=2) > 30).mean() >= 0.02:
            raise AssertionError("too many BGR pixels exceed the bounded colour error")
    return {"status": "ok"}


def _main() -> dict[str, object]:
    if len(sys.argv) != 2 or sys.argv[1] != "preview_export_contract":
        raise SystemExit("usage: run_m5_render_case.py preview_export_contract")
    return _preview_export_contract()


if __name__ == "__main__":
    try:
        print(json.dumps(_main(), ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False))
        raise
