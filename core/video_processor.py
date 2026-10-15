"""Video metadata helpers and shared x264 parameter defaults."""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Tuple

from PyQt6.QtCore import QProcess
from PyQt6.QtNetwork import QLocalSocket

from core.media_tools import MediaToolchain
from utils.file_utils import get_app_dir

logger = logging.getLogger(__name__)

X264_PARAMS = (
    "partitions=all"
    ":rc-lookahead=150"
    ":bframes=16:b-adapt=2"
    ":me=umh:subme=9:merange=48"
    ":no-fast-pskip=1:direct=auto:no-weightb=0"
    ":keyint=300:min-keyint=5:ref=16"
    ":chroma-qp-offset=-3"
    ":aq-mode=1:aq-strength=0.6:trellis=2"
    ":deblock=1,1:psy-rd=0.4,0"
)

X264_CLI_ARGS = [
    "--partitions",
    "all",
    "--rc-lookahead",
    "150",
    "--bframes",
    "16",
    "--b-adapt",
    "2",
    "--me",
    "umh",
    "--subme",
    "9",
    "--merange",
    "48",
    "--no-fast-pskip",
    "--direct",
    "auto",
    "--keyint",
    "300",
    "--min-keyint",
    "5",
    "--ref",
    "16",
    "--chroma-qp-offset",
    "-3",
    "--aq-mode",
    "1",
    "--aq-strength",
    "0.6",
    "--trellis",
    "2",
    "--deblock",
    "1:1",
    "--psy-rd",
    "0.4:0",
]

MPV_METADATA_PROPERTIES = (
    "width",
    "height",
    "dwidth",
    "dheight",
    "duration",
    "container-fps",
    "estimated-vf-fps",
    "fps",
    "estimated-frame-count",
    "video-codec",
)


def find_mpv() -> str:
    """Find the bundled or PATH-provided mpv executable."""
    toolchain = MediaToolchain.discover(get_app_dir())
    return toolchain.mpv_path


@dataclass
class VideoInfo:
    """Basic video stream information."""

    width: int
    height: int
    duration: float
    fps: float
    total_frames: int
    codec: str


def parse_mpv_video_info(properties: dict[str, Any]) -> VideoInfo:
    """Build ``VideoInfo`` from mpv JSON IPC properties."""
    width = _parse_int(_first_value(properties, "width", "dwidth"))
    height = _parse_int(_first_value(properties, "height", "dheight"))
    if width <= 0 or height <= 0:
        raise ValueError("mpv did not report video dimensions")

    duration = _parse_float(properties.get("duration"))
    fps = _parse_float(
        _first_value(properties, "container-fps", "estimated-vf-fps", "fps"),
        default=30.0,
    )
    if fps <= 0:
        fps = 30.0

    total_frames = _parse_int(properties.get("estimated-frame-count"))
    if total_frames <= 0 and duration > 0:
        total_frames = max(1, round(duration * fps))
    if total_frames <= 0:
        total_frames = 1

    codec = str(properties.get("video-codec") or "")
    return VideoInfo(
        width=width,
        height=height,
        duration=duration,
        fps=fps,
        total_frames=total_frames,
        codec=codec,
    )


class VideoProcessor:
    """Probe video metadata through mpv JSON IPC."""

    def __init__(self, mpv_path: str = "") -> None:
        self.mpv_path = mpv_path or find_mpv() or "mpv"

    def check_mpv_available(self) -> Tuple[bool, str]:
        """Return whether mpv is callable."""
        process = QProcess()
        process.setProgram(self.mpv_path)
        process.setArguments(["--version"])
        process.start()
        if not process.waitForStarted(5000):
            return False, "mpv was not found"
        process.waitForFinished(5000)
        output = bytes(process.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        if process.exitCode() != 0:
            return False, "mpv returned a non-zero exit code"
        return True, output.splitlines()[0] if output else "mpv"

    def get_video_info(self, input_path: str) -> Optional[VideoInfo]:
        """Return metadata for the first video stream in ``input_path``."""
        if not Path(input_path).exists():
            logger.error("Video file does not exist: %s", input_path)
            return None
        try:
            properties = _MpvMetadataSession(self.mpv_path).probe(input_path)
            return parse_mpv_video_info(properties)
        except Exception as exc:
            logger.error("mpv metadata probe failed for %s: %s", input_path, exc)
            return None


class _MpvMetadataSession:
    def __init__(self, mpv_path: str) -> None:
        self.mpv_path = mpv_path
        self.ipc_server = _make_mpv_ipc_server()
        self.process: Optional[QProcess] = None
        self.socket: Optional[QLocalSocket] = None
        self._request_id = 1

    def probe(self, input_path: str) -> dict[str, Any]:
        try:
            self.process = QProcess()
            self.process.setProgram(self.mpv_path)
            self.process.setArguments(
                [
                    "--no-config",
                    "--force-window=no",
                    "--idle=no",
                    "--pause=yes",
                    "--keep-open=yes",
                    "--ao=null",
                    "--vo=null",
                    f"--input-ipc-server={self.ipc_server}",
                    input_path,
                ]
            )
            self.process.start()
            if not self.process.waitForStarted(5000):
                raise RuntimeError(
                    f"mpv failed to start: {self.process.errorString()}"
                )

            self.socket = QLocalSocket()
            self._connect_socket()
            self._wait_for_file_loaded()

            properties = {}
            for name in MPV_METADATA_PROPERTIES:
                properties[name] = self._get_property(name)
            return properties
        finally:
            self.close()

    def _connect_socket(self) -> None:
        time.sleep(0.2)
        for _ in range(75):
            if self.socket is None:
                self.socket = QLocalSocket()
            self.socket.connectToServer(self.ipc_server)
            if self.socket.waitForConnected(200):
                return
            self.socket.abort()
            self.socket.deleteLater()
            self.socket = None
        raise RuntimeError("mpv JSON IPC connection was not established")

    def _wait_for_file_loaded(self) -> None:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            message = self._read_message(deadline)
            if not message:
                continue
            if message.get("event") == "file-loaded":
                return
            if message.get("event") == "end-file" and message.get("reason") == "error":
                raise RuntimeError("mpv failed to load the media file")

    def _get_property(self, name: str) -> Any:
        assert self.socket is not None
        request_id = self._request_id
        self._request_id += 1
        payload = json.dumps(
            {"command": ["get_property", name], "request_id": request_id},
            separators=(",", ":"),
        )
        self.socket.write((payload + "\n").encode("utf-8"))
        self.socket.waitForBytesWritten(500)

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            message = self._read_message(deadline)
            if message and message.get("request_id") == request_id:
                if message.get("error") == "success":
                    return message.get("data")
                return None
        return None

    def _read_message(self, deadline: float) -> Optional[dict[str, Any]]:
        assert self.socket is not None
        if not self.socket.canReadLine():
            remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
            if not self.socket.waitForReadyRead(remaining_ms):
                return None
        line = bytes(self.socket.readLine()).decode("utf-8", errors="replace").strip()
        if not line:
            return None
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None

    def close(self) -> None:
        socket = self.socket
        self.socket = None
        if socket is not None:
            try:
                socket.write(b'{"command":["quit"]}\n')
                socket.waitForBytesWritten(100)
                socket.disconnectFromServer()
            except Exception:
                pass

        process = self.process
        self.process = None
        if process is not None:
            try:
                if process.state() != QProcess.ProcessState.NotRunning:
                    process.waitForFinished(1000)
                if process.state() != QProcess.ProcessState.NotRunning:
                    process.kill()
                    process.waitForFinished(3000)
            except Exception:
                pass

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def _make_mpv_ipc_server() -> str:
    name = f"neo_assetmaker_probe_{os.getpid()}_{uuid.uuid4().hex}"
    if sys.platform == "win32":
        return name  # Qt QLocalSocket 会自动添加 \\.\pipe\ 前缀
    return os.path.join(tempfile.gettempdir(), name)


def _first_value(properties: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = properties.get(name)
        if value not in (None, "", "N/A"):
            return value
    return None


def _parse_float(value: Any, default: float = 0.0) -> float:
    if value in (None, "", "N/A"):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_int(value: Any, default: int = 0) -> int:
    if value in (None, "", "N/A"):
        return default
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default
