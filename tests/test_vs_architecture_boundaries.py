"""M7：禁止旧双图实现、旧配置和宿主进程 VS 绑定回流。"""

from __future__ import annotations

import ast
import json
import tempfile
import unittest
from pathlib import Path

from core.vs_runtime.migration import (
    IGNORED_FILTER_FIELDS,
    LEGACY_FIELD_MAP,
    migrate_legacy_vsconfig_once,
)


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_FILES = (
    "core/vs_graph.py",
    "core/vs_script.py",
    "core/vs_engine.py",
    "core/vs_player.py",
    "core/vs_frame.py",
    "config/vsconfig.py",
    "config/vsconfig.json",
    "schemas/vsconfig.schema.json",
)
WORKER_ONLY_FILES = {
    ROOT / "core" / "vs_runtime" / "vs_loader.py",
    ROOT / "core" / "vs_runtime" / "worker_main.py",
    ROOT / "vs_worker.py",
}
MIGRATION_FILE = ROOT / "core" / "vs_runtime" / "migration.py"
FORBIDDEN_IMPORTS = {
    "core.vs_graph",
    "core.vs_script",
    "core.vs_engine",
    "core.vs_player",
    "core.vs_frame",
    "config.vsconfig",
}
FORBIDDEN_REFERENCES = (
    "build_export_graph",
    "write_vpy_script",
    "VpyScriptBuilder",
    "vsconfig.json",
    "resampler_kernel",
    "image_source_format",
)
LEGACY_MIGRATION_REFERENCE_ALLOWLIST = {
    MIGRATION_FILE: frozenset(
        {
            "vsconfig.json",
            "resampler_kernel",
            "image_source_format",
        }
    ),
}


def _production_python_files() -> tuple[Path, ...]:
    roots = (
        ROOT / "config",
        ROOT / "core",
        ROOT / "gui",
        ROOT / "utils",
        ROOT / "_mext",
    )
    files = [ROOT / "main.py", ROOT / "build.py"]
    for root in roots:
        files.extend(root.rglob("*.py"))
    return tuple(sorted(path.resolve() for path in files if path.is_file()))


def _imported_modules(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
    return tuple(modules)


class VSArchitectureBoundaryTests(unittest.TestCase):
    def test_retired_files_are_absent(self):
        present = [relative for relative in FORBIDDEN_FILES if (ROOT / relative).exists()]
        self.assertEqual(present, [], f"M7 仍遗留旧 VS 文件: {present}")

    def test_production_code_does_not_import_retired_modules(self):
        violations: list[str] = []
        for path in _production_python_files():
            for module in _imported_modules(path):
                if module in FORBIDDEN_IMPORTS:
                    violations.append(f"{path.relative_to(ROOT)}: {module}")
        self.assertEqual(violations, [])

    def test_production_code_does_not_reference_retired_graph_api(self):
        violations: list[str] = []
        for path in _production_python_files():
            text = path.read_text(encoding="utf-8")
            allowed = LEGACY_MIGRATION_REFERENCE_ALLOWLIST.get(path, frozenset())
            for reference in FORBIDDEN_REFERENCES:
                if reference in text and reference not in allowed:
                    violations.append(f"{path.relative_to(ROOT)}: {reference}")
        self.assertEqual(violations, [])

    def test_main_process_sources_do_not_import_vapoursynth(self):
        violations: list[str] = []
        for path in _production_python_files():
            if path in WORKER_ONLY_FILES:
                continue
            for module in _imported_modules(path):
                if module == "vapoursynth" or module.startswith("vapoursynth."):
                    violations.append(f"{path.relative_to(ROOT)}: {module}")
        self.assertEqual(violations, [])

    def test_migration_accepts_only_known_legacy_runtime_fields(self):
        self.assertEqual(
            LEGACY_FIELD_MAP,
            {
                "core.num_threads": "core.num_threads",
                "core.max_cache_size_mb": "core.max_cache_size_mb",
                "extra_plugin_dirs": "plugins.native_plugin_dirs",
            },
        )
        self.assertEqual(
            IGNORED_FILTER_FIELDS,
            (
                "required_plugins",
                "image_source_format",
                "output_format",
                "resampler_kernel",
                "colour",
            ),
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            legacy = root / "vsconfig.json"
            target = root / "vapoursynth" / "vs_runtime.user.json"
            marker = root / "vapoursynth" / "vsconfig.migration.json"
            legacy.write_text(
                json.dumps(
                    {
                        "core": {"num_threads": 4, "max_cache_size_mb": 512},
                        "extra_plugin_dirs": [r"D:\VS\plugins"],
                        "core.num_threads": 99,
                        "plugins": {"native_plugin_dirs": [r"D:\\decoy"]},
                        "resampler_kernel_shadow": "Lanczos",
                        "unrelated": {"num_threads": 77},
                    }
                ),
                encoding="utf-8",
            )

            report = migrate_legacy_vsconfig_once(legacy, target, marker)

            self.assertEqual(
                report.migrated_fields,
                ("core.num_threads", "core.max_cache_size_mb", "extra_plugin_dirs"),
            )
            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")),
                {
                    "core": {"num_threads": 4, "max_cache_size_mb": 512},
                    "plugins": {"native_plugin_dirs": [r"D:\VS\plugins"]},
                },
            )


if __name__ == "__main__":
    unittest.main()
