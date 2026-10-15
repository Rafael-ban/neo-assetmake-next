import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import build


class BuildObfuscationTests(unittest.TestCase):
    def test_parse_args_accepts_obfuscate_flag(self):
        args = build.parse_args(["--obfuscate", "--no-installer"])

        self.assertTrue(args.obfuscate)
        self.assertTrue(args.no_installer)

    def test_pyarmor_runtime_packages_returns_runtime_directories(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_dir = root / "pyarmor_runtime_000000"
            runtime_dir.mkdir()
            (root / "pyarmor_runtime_file.py").write_text("", encoding="utf-8")

            self.assertEqual(
                build._pyarmor_runtime_packages(str(root)),
                [("pyarmor_runtime_000000", str(runtime_dir))],
            )

    def test_prepare_obfuscated_source_rejects_incomplete_pyarmor_output(self):
        with TemporaryDirectory() as temp_dir:
            source_root = Path(temp_dir) / "pyarmor-src"

            def write_incomplete_output(*_args, **_kwargs):
                source_root.mkdir(parents=True, exist_ok=True)
                (source_root / "main.py").write_text("# obfuscated\n", encoding="utf-8")
                (source_root / "pyarmor_runtime_000000").mkdir()

            with patch.object(build, "OBFUSCATION_DIR", str(source_root)):
                with patch.object(build, "_find_pyarmor_executable", return_value="pyarmor"):
                    with patch("build.subprocess.run", side_effect=write_incomplete_output) as run:
                        result = build.prepare_obfuscated_source()

        run.assert_called_once()
        self.assertIsNone(result)

    def test_prepare_obfuscated_source_requires_pyarmor_runtime(self):
        with TemporaryDirectory() as temp_dir:
            source_root = Path(temp_dir) / "pyarmor-src"

            def write_output_without_runtime(*_args, **_kwargs):
                for entry in build.OBFUSCATABLE_ENTRIES:
                    entry_path = source_root / entry
                    if entry_path.suffix:
                        entry_path.parent.mkdir(parents=True, exist_ok=True)
                        entry_path.write_text("# obfuscated\n", encoding="utf-8")
                    else:
                        entry_path.mkdir(parents=True, exist_ok=True)

            with patch.object(build, "OBFUSCATION_DIR", str(source_root)):
                with patch.object(build, "_find_pyarmor_executable", return_value="pyarmor"):
                    with patch("build.subprocess.run", side_effect=write_output_without_runtime):
                        result = build.prepare_obfuscated_source()

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
