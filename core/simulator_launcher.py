"""Launch helpers for the Rust simulator."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys


@dataclass(frozen=True)
class MediaLaunchState:
    cropbox: tuple[int, int, int, int]
    rotation: int
    start_frame: int
    end_frame: int


@dataclass(frozen=True)
class SimulatorLaunchRequest:
    config_path: str
    base_dir: str
    app_dir: str
    theme: str
    loop: MediaLaunchState
    intro: MediaLaunchState | None = None


class SimulatorLauncher:
    """Resolve simulator paths and launch the CLI-compatible simulator mode."""

    def __init__(self, app_dir: str | os.PathLike[str]):
        self.app_dir = Path(app_dir)

    def find_simulator(self) -> Path | None:
        candidates = [
            self.app_dir / "simulator" / "arknights_pass_simulator.exe",
            self.app_dir
            / "simulator"
            / "target"
            / "release"
            / "arknights_pass_simulator.exe",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def build_command(
        self,
        simulator_path: str | os.PathLike[str],
        request: SimulatorLaunchRequest,
    ) -> list[str]:
        loop_end_frame = max(request.loop.start_frame, request.loop.end_frame - 1)
        command = [
            str(simulator_path),
            "--config",
            request.config_path,
            "--base-dir",
            request.base_dir,
            "--app-dir",
            request.app_dir,
            "--loop-cropbox",
            _format_cropbox(request.loop.cropbox),
            "--loop-rotation",
            str(request.loop.rotation),
            "--loop-start-frame",
            str(request.loop.start_frame),
            "--loop-end-frame",
            str(loop_end_frame),
            "--theme",
            request.theme,
        ]
        if request.intro is not None:
            intro_end_frame = max(request.intro.start_frame, request.intro.end_frame - 1)
            command.extend(
                [
                    "--intro-cropbox",
                    _format_cropbox(request.intro.cropbox),
                    "--intro-rotation",
                    str(request.intro.rotation),
                    "--intro-start-frame",
                    str(request.intro.start_frame),
                    "--intro-end-frame",
                    str(intro_end_frame),
                ]
            )
        return command

    def build_ipc_command(
        self,
        simulator_path: str | os.PathLike[str],
        request: SimulatorLaunchRequest,
    ) -> list[str]:
        command = self.build_command(simulator_path, request)
        command.append("--stdio")
        return command

    def build_environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PATH"] = str(self.app_dir) + os.pathsep + env.get("PATH", "")
        return env

    def launch_cli(
        self,
        request: SimulatorLaunchRequest,
        simulator_path: str | os.PathLike[str] | None = None,
    ) -> subprocess.Popen:
        resolved_path = Path(simulator_path) if simulator_path else self.find_simulator()
        if resolved_path is None:
            raise FileNotFoundError("Simulator executable was not found")

        popen_kwargs: dict[str, object] = {
            "stderr": subprocess.PIPE,
            "cwd": str(self.app_dir),
            "env": self.build_environment(),
        }
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        return subprocess.Popen(
            self.build_command(resolved_path, request),
            **popen_kwargs,
        )


def _format_cropbox(cropbox: tuple[int, int, int, int]) -> str:
    return f"{cropbox[0]},{cropbox[1]},{cropbox[2]},{cropbox[3]}"
