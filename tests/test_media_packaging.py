import unittest
from pathlib import Path


class MediaPackagingTests(unittest.TestCase):
    def setUp(self):
        self.build_source = Path("build.py").read_text(encoding="utf-8")

    def test_build_script_does_not_package_removed_media_dependencies(self):
        # mpv.exe joined this list when preview moved to in-process VapourSynth:
        # it was ~117MB of the installer and nothing loads it any more.
        for removed_token in ("ffmpeg.exe", "ffprobe.exe", "av.libs", "ffmpeg-sdk",
                              "mpv.exe"):
            with self.subTest(removed_token=removed_token):
                self.assertNotIn(removed_token, self.build_source)

    def test_build_script_keeps_media_tool_candidates(self):
        expected_tokens = (
            "VSPipe.exe",
            "x264-7mod.exe",
            "mp4box.exe",
            "lsmash",
        )
        for expected_token in expected_tokens:
            with self.subTest(expected_token=expected_token):
                self.assertIn(expected_token, self.build_source)

    def test_build_script_packages_runtime_and_legacy_vs_configs(self):
        # M1 ships the strict runtime config while retaining legacy vsconfig
        # until its one-time migration has completed in installed builds.
        self.assertIn("config/vs_runtime.json", self.build_source)
        self.assertIn("config/vsconfig.json", self.build_source)

    def test_build_script_packages_runtime_contract_schemas(self):
        self.assertIn("schemas/vs_runtime.schema.json", self.build_source)
        self.assertIn("schemas/vs_job.schema.json", self.build_source)

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

    def test_runtime_source_has_no_executable_mpv_references(self):
        """Regression lock: mpv must not come back as a code dependency.

        Matches *identifiers and command strings*, not the word "mpv" — several
        docstrings still explain why the VapourSynth design replaced the mpv one,
        and that history is worth keeping.
        """
        disallowed_tokens = (
            "mpv_path",           # toolchain field / constructor arg
            "mpv.exe",
            "_mpv_process",
            "_mpv_socket",
            "_send_mpv_command",
            "_MpvMetadataSession",
            "_MpvSurface",
            "input-ipc-server",   # mpv CLI
            "screenshot-to-file",
            "video-rotate",
            "MpvLaunchWorker",
        )
        offenders = []
        for root in (Path("core"), Path("gui"), Path("config"), Path("utils"),
                     Path("main.py"), Path("build.py")):
            paths = [root] if root.is_file() else root.rglob("*.py")
            for path in paths:
                text = path.read_text(encoding="utf-8", errors="ignore")
                for token in disallowed_tokens:
                    if token in text:
                        offenders.append(f"{path}:{token}")

        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
