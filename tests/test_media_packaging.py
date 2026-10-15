import unittest
from pathlib import Path


class MediaPackagingTests(unittest.TestCase):
    def setUp(self):
        self.build_source = Path("build.py").read_text(encoding="utf-8")

    def test_build_script_does_not_package_removed_media_dependencies(self):
        for removed_token in ("ffmpeg.exe", "ffprobe.exe", "av.libs", "ffmpeg-sdk"):
            with self.subTest(removed_token=removed_token):
                self.assertNotIn(removed_token, self.build_source)

    def test_build_script_keeps_media_tool_candidates(self):
        expected_tokens = (
            "mpv.exe",
            "VSPipe.exe",
            "x264-7mod.exe",
            "mp4box.exe",
            "lsmash",
        )
        for expected_token in expected_tokens:
            with self.subTest(expected_token=expected_token):
                self.assertIn(expected_token, self.build_source)

    def test_runtime_source_does_not_reference_removed_ffmpeg_stack(self):
        disallowed_tokens = (
            "import av",
            "av.open",
            "av.VideoFrame",
            "ffmpeg-next",
            "ffmpeg-sdk",
            "ffmpeg.exe",
            "ffprobe",
            "libx264",
        )
        source_roots = (
            Path("core"),
            Path("gui"),
            Path("simulator") / "src",
            Path("simulator") / "Cargo.toml",
            Path("pyproject.toml"),
        )
        offenders = []

        for root in source_roots:
            paths = [root] if root.is_file() else root.rglob("*")
            for path in paths:
                if path.suffix not in {".py", ".rs", ".toml"}:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                for token in disallowed_tokens:
                    if token in text:
                        offenders.append(f"{path}:{token}")

        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
