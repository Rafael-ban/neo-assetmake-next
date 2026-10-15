import sys
import tempfile
from pathlib import Path
import unittest

from core.simulator_ipc import SimulatorIpcClient
from core.simulator_launcher import (
    MediaLaunchState,
    SimulatorLauncher,
    SimulatorLaunchRequest,
)


class SimulatorLauncherTests(unittest.TestCase):
    def make_request(self):
        return SimulatorLaunchRequest(
            config_path="project/epconfig.json",
            base_dir="project",
            app_dir="app",
            theme="dark",
            loop=MediaLaunchState(
                cropbox=(1, 2, 3, 4),
                rotation=90,
                start_frame=10,
                end_frame=20,
            ),
            intro=MediaLaunchState(
                cropbox=(5, 6, 7, 8),
                rotation=180,
                start_frame=3,
                end_frame=9,
            ),
        )

    def test_find_simulator_prefers_packaged_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app_dir = Path(temp_dir)
            packaged = app_dir / "simulator" / "arknights_pass_simulator.exe"
            dev = app_dir / "simulator" / "target" / "release" / "arknights_pass_simulator.exe"
            packaged.parent.mkdir(parents=True)
            dev.parent.mkdir(parents=True)
            packaged.write_text("", encoding="utf-8")
            dev.write_text("", encoding="utf-8")

            launcher = SimulatorLauncher(app_dir)

            self.assertEqual(launcher.find_simulator(), packaged)

    def test_build_command_includes_loop_and_intro_state(self):
        launcher = SimulatorLauncher("app")

        command = launcher.build_command("sim.exe", self.make_request())

        self.assertEqual(command[0], "sim.exe")
        self.assertIn("--loop-cropbox", command)
        self.assertIn("1,2,3,4", command)
        self.assertIn("--loop-end-frame", command)
        self.assertIn("19", command)
        self.assertIn("--intro-cropbox", command)
        self.assertIn("5,6,7,8", command)
        self.assertIn("--theme", command)
        self.assertIn("dark", command)

    def test_build_environment_adds_app_path_without_ffmpeg_sdk(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app_dir = Path(temp_dir)
            sdk_bin = app_dir / "ffmpeg-sdk" / "bin"
            sdk_bin.mkdir(parents=True)
            launcher = SimulatorLauncher(app_dir)

            env = launcher.build_environment()

        self.assertTrue(env["PATH"].startswith(str(app_dir)))
        self.assertNotIn(str(sdk_bin), env["PATH"])

    def test_build_ipc_command_enables_stdio(self):
        launcher = SimulatorLauncher("app")

        command = launcher.build_ipc_command("sim.exe", self.make_request())

        self.assertEqual(command[-1], "--stdio")
        self.assertIn("--theme", command)


class SimulatorIpcClientTests(unittest.TestCase):
    def test_client_reads_ready_sends_control_and_shutdown(self):
        script = """
import json
import sys

print(json.dumps({"type": "ready"}), flush=True)
for line in sys.stdin:
    message = json.loads(line)
    if message["type"] == "control":
        print(
            json.dumps({
                "type": "state_update",
                "payload": {
                    "state": 1,
                    "frame": 10,
                    "is_playing": message.get("payload") == "play",
                },
            }),
            flush=True,
        )
    elif message["type"] == "shutdown":
        break
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            script_path = Path(temp_dir) / "fake_simulator.py"
            script_path.write_text(script, encoding="utf-8")
            client = SimulatorIpcClient([sys.executable, str(script_path)])
            client.start()

            ready = client.read_message(timeout=2)
            self.assertEqual({"type": "ready"}, ready)

            client.control("play")
            update = client.read_message(timeout=2)
            self.assertEqual(update["type"], "state_update")
            self.assertTrue(update["payload"]["is_playing"])

            self.assertEqual(client.shutdown(timeout=2), 0)


if __name__ == "__main__":
    unittest.main()
