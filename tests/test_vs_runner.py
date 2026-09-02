from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import uuid
from pathlib import Path
from unittest import mock

from core.media_tools import MediaToolchain, build_media_subprocess_env
from core.vs_runtime.job import RenderJobError, load_render_job
from core.vs_runtime.script_header import ScriptHeaderError, parse_script_header


ROOT = Path(__file__).resolve().parents[1]
HELPER_ROOT = ROOT / "resources" / "vapoursynth" / "python"
RUNNER = ROOT / "resources" / "vapoursynth" / "assetmaker_runner.vpy"
FIXTURES = ROOT / "tests" / "fixtures" / "vs_scripts"
CHILD = ROOT / "tests" / "helpers" / "run_vs_contract_case.py"
TOOLCHAIN = MediaToolchain.discover(str(ROOT))


def _import_executor():
    if str(HELPER_ROOT) not in sys.path:
        sys.path.insert(0, str(HELPER_ROOT))
    try:
        return importlib.import_module("assetmaker_vs.executor")
    except ModuleNotFoundError as exc:
        raise AssertionError("portable assetmaker_vs.executor 尚未实现") from exc


def _write_job(path: Path) -> None:
    root = path.parent.resolve()
    payload = {
        "api_version": 1,
        "epoch": 3,
        "track": "loop",
        "project_root": str(root),
        "source": {
            "path": str(root / "source.mp4"),
            "kind": "video",
            "virtual_frame_count": None,
        },
        "timeline": {
            "start_frame": 0,
            "end_frame": 3,
            "fps": {"numerator": 30000, "denominator": 1001},
        },
        "transform": {
            "rotation": 0,
            "crop": {
                "coordinate_space": "post_rotation_source_pixels",
                "x": 0,
                "y": 0,
                "width": 0,
                "height": 0,
            },
        },
        "output": {
            "profile": "360x640",
            "display_width": 360,
            "display_height": 640,
            "coded_width": 384,
            "coded_height": 640,
            "pixel_format": "YUV420P8",
            "matrix": "170m",
            "transfer": "170m",
            "primaries": "170m",
            "range": "limited",
            "final_rotate_180": False,
        },
        "paths": {"cache_dir": str(root / "cache")},
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _run_child(case: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHILD), case],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )


def _run_vspipe(
    *, script: Path, job: Path, mode: str
) -> subprocess.CompletedProcess[str]:
    args = [
        TOOLCHAIN.vspipe_path,
        "--info",
        "--arg",
        f"assetmaker_job={job}",
        "--arg",
        f"assetmaker_script={script}",
        "--arg",
        "assetmaker_api=1",
        "--arg",
        f"assetmaker_mode={mode}",
        str(RUNNER),
        "-",
    ]
    kwargs: dict[str, object] = {
        "cwd": ROOT,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": 30,
        "check": False,
        "env": build_media_subprocess_env(TOOLCHAIN.vspipe_path),
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(args, **kwargs)


class PortableExecutorTests(unittest.TestCase):
    def test_helper_is_never_evicted_even_under_the_script_root(self):
        executor = _import_executor()
        name = "resources.vapoursynth.python.assetmaker_vs.synthetic_test"
        module = types.ModuleType(name)
        module.__file__ = str(HELPER_ROOT / "assetmaker_vs" / "synthetic.py")
        sys.modules[name] = module
        self.addCleanup(sys.modules.pop, name, None)

        removed = executor.evict_modules_under(HELPER_ROOT.parent)

        self.assertNotIn(name, removed)
        self.assertIs(sys.modules[name], module)

    def test_module_search_order_is_deterministic(self):
        executor = _import_executor()
        project = Path(tempfile.gettempdir()).resolve() / "素材" / "黍"
        third_party = project / "third_party"

        actual = executor.build_module_search_paths(
            script_path=project / "pipeline.vpy",
            runtime_dirs=[third_party],
        )

        self.assertEqual(
            actual,
            (
                project,
                project / "modules",
                third_party,
                HELPER_ROOT,
            ),
        )

    def test_module_search_paths_deduplicate_without_reordering(self):
        executor = _import_executor()
        project = Path(tempfile.gettempdir()).resolve() / "assetmaker-dedupe"

        actual = executor.build_module_search_paths(
            script_path=project / "pipeline.vpy",
            runtime_dirs=[project / "modules", project, project / "extra"],
        )

        self.assertEqual(
            actual,
            (project, project / "modules", project / "extra", HELPER_ROOT),
        )

    def test_runtime_python_dirs_use_dedicated_json_environment(self):
        executor = _import_executor()
        first = str((Path(tempfile.gettempdir()) / "python-a").resolve())
        second = str((Path(tempfile.gettempdir()) / "python-b").resolve())
        with mock.patch.dict(
            os.environ,
            {"ASSETMAKER_VS_PYTHON_DIRS_JSON": json.dumps([first, second])},
            clear=False,
        ):
            self.assertEqual(
                executor.runtime_python_dirs_from_env(),
                (Path(first), Path(second)),
            )

        with mock.patch.dict(
            os.environ,
            {"ASSETMAKER_VS_PYTHON_DIRS_JSON": json.dumps("not-a-list")},
            clear=False,
        ):
            with self.assertRaises(ValueError):
                executor.runtime_python_dirs_from_env()

    def test_reload_evicts_only_modules_under_script_root(self):
        executor = _import_executor()
        root_name = f"assetmaker_reload_{uuid.uuid4().hex}"
        external_name = f"assetmaker_external_{uuid.uuid4().hex}"
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir).resolve()
            script_root = base / "script"
            external_root = base / "external"
            script_root.mkdir()
            external_root.mkdir()
            module_path = script_root / f"{root_name}.py"
            external_path = external_root / f"{external_name}.py"
            module_path.write_text('VALUE = "first"\n', encoding="utf-8")
            external_path.write_text('VALUE = "kept"\n', encoding="utf-8")
            sys.path[:0] = [str(script_root), str(external_root)]
            self.addCleanup(lambda: sys.path.remove(str(script_root)))
            self.addCleanup(lambda: sys.path.remove(str(external_root)))
            first = importlib.import_module(root_name)
            external = importlib.import_module(external_name)
            self.addCleanup(sys.modules.pop, root_name, None)
            self.addCleanup(sys.modules.pop, external_name, None)
            self.assertEqual(first.VALUE, "first")

            module_path.write_text(
                'VALUE = "second-and-different-size"\n', encoding="utf-8"
            )
            future = time.time() + 2
            os.utime(module_path, (future, future))
            for cache_file in script_root.glob("__pycache__/*.pyc"):
                cache_file.unlink()
            executor.evict_modules_under(script_root)
            importlib.invalidate_caches()
            second = importlib.import_module(root_name)

            self.assertEqual(second.VALUE, "second-and-different-size")
            self.assertIs(sys.modules[external_name], external)
            self.assertIn("assetmaker_vs.executor", sys.modules)

    def test_deferred_frame_callback_keeps_import_path_and_stdout_isolated(self):
        result = _run_child("executor_deferred")

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(
            payload["logs"],
            [
                "hello from script",
                "lazy module imported",
                "hello from callback",
            ],
        )
        self.assertTrue(payload["active_during_render"])
        self.assertFalse(payload["active_after_close"])

    def test_stdout_sink_is_thread_safe_and_chunks_below_protocol_limit(self):
        executor = _import_executor()
        lines: list[str] = []
        original = sys.stdout
        writer = executor.install_python_stdout(lines.append)
        try:
            payload = "黍" * 1_500_000
            writer.write(payload + "\n")
            threads = [
                threading.Thread(
                    target=lambda index=index: writer.write(f"thread-{index}\n")
                )
                for index in range(8)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())
            writer.flush()
        finally:
            sys.stdout = original

        payload_chunks = lines[:-8]
        self.assertEqual("".join(payload_chunks), payload)
        self.assertTrue(payload_chunks)
        self.assertTrue(
            all(len(chunk.encode("utf-8")) < 4 * 1024 * 1024 for chunk in lines)
        )
        self.assertEqual(set(lines[-8:]), {f"thread-{i}" for i in range(8)})


class SharedPortableAdapterTests(unittest.TestCase):
    @staticmethod
    def _signature(callable_):
        try:
            callable_()
        except ValueError as exc:
            return (
                getattr(exc, "code", None),
                getattr(exc, "field", None),
                getattr(exc, "path", None),
            )
        raise AssertionError("非法 fixture 未被拒绝")

    def test_header_helper_and_core_adapter_share_error_identity(self):
        if str(HELPER_ROOT) not in sys.path:
            sys.path.insert(0, str(HELPER_ROOT))
        try:
            helper = importlib.import_module("assetmaker_vs.script_header")
        except ModuleNotFoundError as exc:
            self.fail(f"portable script_header 尚未实现: {exc}")
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir).resolve() / "invalid.vpy"
            path.write_text(
                "# assetmaker-api: 1\n"
                "# assetmaker-mode: magic\n"
                "# assetmaker-capabilities: source\n"
                "# assetmaker-requires:\n"
                "# assetmaker-editor-output: 0\n",
                encoding="utf-8",
            )

            helper_error = self._signature(lambda: helper.parse_script_header(path))
            core_error = self._signature(lambda: parse_script_header(path))

        self.assertEqual(helper_error, core_error)
        self.assertEqual(helper_error[0], "header.mode")
        self.assertEqual(helper_error[1], "mode")

    def test_job_helper_and_core_adapter_share_error_identity(self):
        if str(HELPER_ROOT) not in sys.path:
            sys.path.insert(0, str(HELPER_ROOT))
        try:
            helper = importlib.import_module("assetmaker_vs.job_api")
        except ModuleNotFoundError as exc:
            self.fail(f"portable job_api 尚未实现: {exc}")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            path = root / "invalid-job.json"
            _write_job(path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["timeline"]["start_frame"] = 3
            payload["timeline"]["end_frame"] = 3
            path.write_text(json.dumps(payload), encoding="utf-8")

            helper_error = self._signature(lambda: helper.load_job(path))
            core_error = self._signature(lambda: load_render_job(path))

        self.assertEqual(helper_error, core_error)
        self.assertEqual(helper_error[0], "job.timeline.order")
        self.assertEqual(helper_error[1], "timeline.end_frame")

    def test_portable_modules_import_without_project_or_qt_dependencies(self):
        script = """
import builtins
import json
real_import = builtins.__import__
blocked = {"core", "config", "PyQt6"}
def guarded(name, *args, **kwargs):
    if name.split(".", 1)[0] in blocked:
        raise RuntimeError("forbidden import: " + name)
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded
import assetmaker_vs.contract
import assetmaker_vs.display
import assetmaker_vs.executor
import assetmaker_vs.job_api
import assetmaker_vs.script_header
print(json.dumps({"ok": True}))
"""
        env = os.environ.copy()
        env["PYTHONPATH"] = str(HELPER_ROOT)
        with tempfile.TemporaryDirectory() as temp_dir:
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=temp_dir,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=15,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"ok": True})


@unittest.skipUnless(TOOLCHAIN.vspipe_path, "bundled VSPipe unavailable")
class TrustedRunnerTests(unittest.TestCase):
    def test_chinese_script_root_imports_modules_and_keeps_stdout_off_y4m(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir).resolve() / "素材" / "黍"
            modules = project / "modules"
            modules.mkdir(parents=True)
            script = project / "pipeline.vpy"
            shutil.copyfile(FIXTURES / "prints_and_imports.vpy", script)
            (modules / "marker.py").write_text(
                'VALUE = "marker-from-script-root"\n', encoding="utf-8"
            )
            (modules / "lazy_marker.py").write_text(
                'print("lazy module imported")\nVALUE = "lazy-marker"\n',
                encoding="utf-8",
            )
            job = project / "job.json"
            _write_job(job)

            result = _run_vspipe(script=script, job=job, mode="raw")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Width: 384", result.stdout)
        self.assertNotIn("hello from script", result.stdout)
        self.assertIn("hello from script", result.stderr)
        self.assertIn("lazy module imported", result.stderr)

    def test_header_mode_mismatch_fails_before_user_script(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir).resolve() / "素材" / "黍"
            project.mkdir(parents=True)
            script = project / "pipeline.vpy"
            shutil.copyfile(FIXTURES / "raw_valid.vpy", script)
            job = project / "job.json"
            _write_job(job)

            result = _run_vspipe(script=script, job=job, mode="compatible")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invocation.mode", result.stderr)

    def test_bad_output_contract_makes_real_vspipe_fail(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir).resolve() / "素材" / "黍"
            project.mkdir(parents=True)
            script = project / "bad-output.vpy"
            shutil.copyfile(FIXTURES / "compatible_bad_output.vpy", script)
            job = project / "job.json"
            _write_job(job)

            result = _run_vspipe(script=script, job=job, mode="compatible")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("contract.pixel_format", result.stderr)


if __name__ == "__main__":
    unittest.main()
