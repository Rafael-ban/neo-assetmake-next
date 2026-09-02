from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from core.vs_runtime.script_header import parse_script_header


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PIPELINE = ROOT / "resources" / "vapoursynth" / "default_pipeline.vpy"
CHILD = ROOT / "tests" / "helpers" / "run_vs_contract_case.py"


def _run_child(case: str, *, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHILD), case],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


class DefaultPipelineHeaderTests(unittest.TestCase):
    def test_default_header_declares_the_full_compatible_contract(self):
        self.assertTrue(
            DEFAULT_PIPELINE.is_file(), "默认 compatible pipeline 尚未实现"
        )
        header = parse_script_header(DEFAULT_PIPELINE)

        self.assertEqual(header.api_version, 1)
        self.assertEqual(header.mode, "compatible")
        self.assertEqual(header.editor_output, 1)
        self.assertEqual(
            header.capabilities,
            ("source", "trim", "crop", "rotation", "resolution", "image_loop"),
        )
        self.assertEqual(
            header.requires,
            ("lsmas.LWLibavSource", "imwri.Read"),
        )


class DefaultPipelineRealSubprocessTests(unittest.TestCase):
    def test_image_loops_full_editor_timeline_before_nonzero_trim(self):
        result = _run_child("default_image")

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        output0 = payload["output0"]
        output1 = payload["output1"]
        self.assertEqual(
            (output0["width"], output0["height"]), (384, 640)
        )
        self.assertEqual(output0["num_frames"], 5)
        self.assertEqual(output0["fps"], [30, 1])
        self.assertEqual(output0["format"], "YUV420P8")
        self.assertEqual(
            output0["props"],
            {
                "_Matrix": 6,
                "_Transfer": 6,
                "_Primaries": 6,
                "_ColorRange": 1,
            },
        )
        self.assertEqual(
            (output1["width"], output1["height"]), (6, 8)
        )
        self.assertEqual(output1["num_frames"], 9)
        self.assertEqual(output1["fps"], [30, 1])
        self.assertEqual(payload["runner"]["returncode"], 0)
        self.assertIn("Width: 384", payload["runner"]["stdout"])
        self.assertGreater(payload["encoded"]["size"], 0)

    def test_video_bootstrap_and_resolved_jobs_share_full_editor_output(self):
        result = _run_child("default_video")

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(
            (payload["bootstrap0"]["width"], payload["bootstrap0"]["height"]),
            (720, 1080),
        )
        self.assertEqual(payload["bootstrap0"]["num_frames"], 8)
        self.assertEqual(payload["bootstrap0"]["fps"], [30000, 1001])
        self.assertEqual(payload["resolved0"]["num_frames"], 5)
        self.assertEqual(payload["resolved0"]["fps"], [30000, 1001])
        for key in ("bootstrap1", "resolved1"):
            self.assertEqual(
                (payload[key]["width"], payload[key]["height"]), (8, 12)
            )
            self.assertEqual(payload[key]["num_frames"], 8)
            self.assertEqual(payload[key]["fps"], [30000, 1001])
        self.assertEqual(
            payload["resolved0"]["props"],
            {
                "_Matrix": 6,
                "_Transfer": 6,
                "_Primaries": 6,
                "_ColorRange": 1,
            },
        )

    def test_true_709_untagged_source_does_not_flip_at_p7_crop_height(self):
        result = _run_child("default_p7", timeout=240)

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["source_matrix"], 2)
        self.assertEqual(len(payload["digests"]), 2)
        self.assertTrue(payload["equal"], payload["digests"])


if __name__ == "__main__":
    unittest.main()
