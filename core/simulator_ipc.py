"""Stdio JSON-lines client for the Rust simulator."""

from __future__ import annotations

import json
import logging
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Sequence

from core.simulator_launcher import SimulatorLauncher, SimulatorLaunchRequest

logger = logging.getLogger(__name__)


class SimulatorIpcClient:
    """Manage a simulator process using the stdio IPC protocol."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.command = [str(part) for part in command]
        self.cwd = cwd
        self.env = env
        self._process: subprocess.Popen | None = None
        self._messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stderr_lines: queue.Queue[str] = queue.Queue()
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None

    @classmethod
    def from_launch_request(
        cls,
        launcher: SimulatorLauncher,
        request: SimulatorLaunchRequest,
        *,
        simulator_path: str | Path | None = None,
    ) -> "SimulatorIpcClient":
        resolved_path = Path(simulator_path) if simulator_path else launcher.find_simulator()
        if resolved_path is None:
            raise FileNotFoundError("Simulator executable was not found")

        return cls(
            launcher.build_ipc_command(resolved_path, request),
            cwd=str(launcher.app_dir),
            env=launcher.build_environment(),
        )

    @property
    def process(self) -> subprocess.Popen | None:
        return self._process

    @property
    def returncode(self) -> int | None:
        return self._process.returncode if self._process is not None else None

    def start(self) -> None:
        if self._process is not None:
            return

        popen_kwargs: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "bufsize": 1,
            "cwd": self.cwd,
            "env": self.env,
        }
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        self._process = subprocess.Popen(self.command, **popen_kwargs)
        self._reader_thread = threading.Thread(
            target=self._read_stdout,
            name="simulator-ipc-stdout",
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stderr,
            name="simulator-ipc-stderr",
            daemon=True,
        )
        self._reader_thread.start()
        self._stderr_thread.start()

    def send(self, message_type: str, payload: Any | None = None) -> None:
        process = self._require_process()
        if process.stdin is None:
            raise RuntimeError("Simulator stdin is not available")

        message: dict[str, Any] = {"type": message_type}
        if payload is not None:
            message["payload"] = payload
        process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        process.stdin.flush()

    def load_config(self, config: dict[str, Any], base_dir: str | Path) -> None:
        self.send(
            "load_config",
            {
                "config": config,
                "base_dir": str(base_dir),
            },
        )

    def load_config_file(self, config_path: str | Path, base_dir: str | Path) -> None:
        with open(config_path, "r", encoding="utf-8") as config_file:
            self.load_config(json.load(config_file), base_dir)

    def control(self, command: str | int) -> None:
        if isinstance(command, int):
            payload: str | dict[str, int] = {"seek_to": command}
        else:
            payload = command
        self.send("control", payload)

    def read_message(self, timeout: float | None = None) -> dict[str, Any] | None:
        try:
            return self._messages.get(timeout=timeout)
        except queue.Empty:
            return None

    def read_stderr_line(self, timeout: float | None = None) -> str | None:
        try:
            return self._stderr_lines.get(timeout=timeout)
        except queue.Empty:
            return None

    def shutdown(self, timeout: float = 2.0) -> int | None:
        process = self._process
        if process is None:
            return None

        returncode: int | None
        if process.poll() is None:
            try:
                self.send("shutdown")
            except Exception:
                logger.debug("Failed to send simulator shutdown", exc_info=True)

            try:
                if process.stdin is not None:
                    process.stdin.close()
            except Exception:
                logger.debug("Failed to close simulator stdin", exc_info=True)

            try:
                returncode = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.terminate()
                returncode = process.wait(timeout=timeout)
        else:
            returncode = process.returncode

        self._join_reader_threads()
        self._close_process_streams()
        return returncode

    def _require_process(self) -> subprocess.Popen:
        if self._process is None:
            raise RuntimeError("Simulator IPC client has not been started")
        if self._process.poll() is not None:
            raise RuntimeError("Simulator process has already exited")
        return self._process

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return

        for raw_line in process.stdout:
            line = raw_line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Ignoring non-JSON simulator stdout: %s", line)
                continue
            if isinstance(message, dict):
                self._messages.put(message)

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return

        for raw_line in process.stderr:
            line = raw_line.rstrip()
            if line:
                self._stderr_lines.put(line)

    def _join_reader_threads(self) -> None:
        for thread in (self._reader_thread, self._stderr_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=0.2)

    def _close_process_streams(self) -> None:
        process = self._process
        if process is None:
            return

        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                if stream is not None and not stream.closed:
                    stream.close()
            except Exception:
                logger.debug("Failed to close simulator process stream", exc_info=True)
