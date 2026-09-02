"""V2: byte-identity golden test for the refactored .vpy generator.

The goldens in tests/fixtures/vpy/*.golden were captured from the pre-refactor
`write_vpy_script`. After extracting VS authoring into core/vs_script.py's
`VpyScriptBuilder`, the generated script must remain byte-for-byte identical
(the per-run lsmas cachefile path is normalized to {CACHE} on both sides).
Regenerate the goldens ONLY when the output is deliberately meant to change.
"""
import re
import tempfile
import unittest
from pathlib import Path

from core.media_pipeline import write_vpy_script  # re-exported from core.vs_script
from core.export_service import VideoExportParams


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "vpy"
_CACHE_RE = re.compile(r"cachefile=[^)]*\)")

# Must match tests/fixtures/vpy capture matrix exactly.
CASES = {
    "video_360x640_trim_crop_rot180": VideoExportParams(
        video_path=r"C:\media\loop.mp4", cropbox=(10, 20, 100, 200),
        start_frame=5, end_frame=35, fps=30.0, resolution="360x640", rotation=180,
    ),
    "video_360x640_degenerate_trim": VideoExportParams(
        video_path=r"C:\media\loop.mp4", cropbox=(0, 0, 0, 0),
        start_frame=5, end_frame=5, fps=30.0, resolution="360x640",
    ),
    "image_360x640_crop_rot90": VideoExportParams(
        video_path=r"C:\media\bg.png", cropbox=(10, 20, 100, 200),
        start_frame=0, end_frame=30, fps=30.0, resolution="360x640",
        is_image=True, rotation=90,
    ),
    "image_720x1080": VideoExportParams(
        video_path=r"C:\media\logo.png", cropbox=(0, 0, 0, 0),
        start_frame=0, end_frame=30, fps=30.0, resolution="720x1080", is_image=True,
    ),
    "video_720x1080": VideoExportParams(
        video_path=r"C:\media\hd.mp4", cropbox=(0, 0, 0, 0),
        start_frame=0, end_frame=120, fps=30.0, resolution="720x1080",
    ),
}


def _generate_normalized(params) -> str:
    with tempfile.TemporaryDirectory() as d:
        script = Path(d) / "s.vpy"
        write_vpy_script(str(script), params)
        text = script.read_text(encoding="utf-8")
    return _CACHE_RE.sub("cachefile={CACHE})", text)


class VpyGoldenTests(unittest.TestCase):
    def test_generator_output_is_byte_identical_to_pre_refactor(self):
        for name, params in CASES.items():
            with self.subTest(case=name):
                golden = (FIXTURES / f"{name}.golden").read_text(encoding="utf-8")
                self.assertEqual(
                    _generate_normalized(params), golden,
                    f"{name}: .vpy output drifted from the pre-refactor golden",
                )

    def test_all_goldens_are_exercised(self):
        on_disk = {p.stem for p in FIXTURES.glob("*.golden")}
        self.assertEqual(on_disk, set(CASES), "golden fixtures and CASES diverged")


if __name__ == "__main__":
    unittest.main()
