import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.vs_runtime.session import compute_script_bundle_hash


_HEADER = """# assetmaker-api: 1
# assetmaker-mode: compatible
# assetmaker-capabilities: source
# assetmaker-requires:
# assetmaker-editor-output: 0
"""


class ScriptBundleTrustTests(unittest.TestCase):
    def _script(self, root: Path) -> Path:
        script = root / "pipeline.vpy"
        script.write_text(_HEADER + "\nVALUE = 1\n", encoding="utf-8")
        return script

    def test_hash_includes_only_code_files_and_rejects_unsafe_bundle_members(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "bundle"
            root.mkdir()
            script = self._script(root)
            helper = root / "modules" / "helper.py"
            helper.parent.mkdir()
            helper.write_text("VALUE = 1\n", encoding="utf-8")
            (root / "lookup.json").write_text("{}", encoding="utf-8")

            initial = compute_script_bundle_hash(script)
            (root / "lookup.json").write_text('{"changed": true}', encoding="utf-8")
            self.assertEqual(compute_script_bundle_hash(script), initial)
            helper.write_text("VALUE = 2\n", encoding="utf-8")
            self.assertNotEqual(compute_script_bundle_hash(script), initial)

            # NTFS 默认把 ASCII ``Case.py`` / ``case.py`` 合并为一个目录项，
            # 无法证明 hash 会拒绝碰撞。ß 与 ss 可以同时存在，Python 的
            # Unicode casefold 却会将二者归一为同一路径键。
            (root / "\u00df.py").write_text("A = 1\n", encoding="utf-8")
            (root / "ss.py").write_text("B = 1\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "大小写碰撞"):
                compute_script_bundle_hash(script)

    def test_hash_rejects_root_escaping_symlink_and_uncanonicalizable_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            root = temp / "bundle"
            root.mkdir()
            script = self._script(root)
            outside = temp / "outside.py"
            outside.write_text("OUTSIDE = 1\n", encoding="utf-8")
            link = root / "escape.py"
            try:
                link.symlink_to(outside)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"当前文件系统不支持测试 symlink: {exc}")

            with self.assertRaisesRegex(ValueError, "逃逸根目录"):
                compute_script_bundle_hash(script)

            with mock.patch(
                "core.vs_runtime.session.Path.resolve", side_effect=OSError("bad")
            ):
                with self.assertRaisesRegex(ValueError, "canonical"):
                    compute_script_bundle_hash(script)

    def test_project_reference_is_relative_and_trust_is_local_to_root_and_hash(self):
        from core.vs_runtime.trust import (
            ProjectTrustStore,
            ScriptReference,
            ScriptTrustError,
            resolve_project_script,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "project"
            script = project / "vapoursynth" / "pipeline.vpy"
            script.parent.mkdir(parents=True)
            script.write_text(_HEADER, encoding="utf-8")
            trust_path = Path(temp_dir) / "appdata" / "trust.json"
            store = ProjectTrustStore(trust_path)
            reference = ScriptReference("project", "vapoursynth/pipeline.vpy")

            resolved = resolve_project_script(project, reference)
            digest = compute_script_bundle_hash(resolved)
            self.assertFalse(store.is_trusted(resolved.parent, digest))
            store.trust(resolved.parent, digest)
            self.assertTrue(store.is_trusted(resolved.parent, digest))
            self.assertEqual(
                json.loads(trust_path.read_text(encoding="utf-8"))["schema_version"],
                1,
            )

            with self.assertRaises(ScriptTrustError):
                resolve_project_script(project, ScriptReference("project", "../outside.vpy"))
            with self.assertRaises(ScriptTrustError):
                ScriptReference("global", "C:/local/pipeline.vpy")


if __name__ == "__main__":
    unittest.main()
