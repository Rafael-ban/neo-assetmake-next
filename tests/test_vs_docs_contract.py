"""M8：用户 VPY 架构的文档与知识库索引契约。"""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
KB = ROOT / "docs" / "vapoursynth-kb"


class VSDocumentationContractTests(unittest.TestCase):
    def test_index_links_the_user_vpy_articles(self):
        index = (KB / "INDEX.md").read_text(encoding="utf-8")
        for filename in (
            "13-user-vpy-abi.md",
            "14-worker-protocol.md",
            "15-output-contract.md",
            "16-script-trust.md",
        ):
            with self.subTest(filename=filename):
                self.assertTrue((KB / filename).is_file())
                self.assertIn(f"]({filename})", index)

    def test_public_docs_link_to_the_user_script_guide(self):
        for relative in ("README.md", "docs/USER_MANUAL.md"):
            with self.subTest(relative=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("13-user-vpy-abi.md", text)

    def test_docs_describe_the_real_user_vpy_contract(self):
        combined = "\n".join(
            (KB / filename).read_text(encoding="utf-8")
            for filename in (
                "13-user-vpy-abi.md",
                "14-worker-protocol.md",
                "15-output-contract.md",
                "16-script-trust.md",
            )
        )
        for expected in (
            "assetmaker-api",
            "assetmaker-mode",
            "assetmaker-editor-output",
            "assetmaker_job",
            "assetmaker_api",
            "assetmaker_mode",
            "output 0",
            "output 1",
            "compatible",
            "raw",
            "VSPipe",
            "vs_worker.exe",
            "portable.vs",
            "bundle hash",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, combined)

    def test_current_docs_do_not_describe_the_retired_configuration_model(self):
        documents = {
            "README.md": (ROOT / "README.md").read_text(encoding="utf-8"),
            "USER_MANUAL.md": (ROOT / "docs" / "USER_MANUAL.md").read_text(
                encoding="utf-8"
            ),
            "VS_DECOUPLING.md": (ROOT / "docs" / "VS_DECOUPLING.md").read_text(
                encoding="utf-8"
            ),
            "INDEX.md": (KB / "INDEX.md").read_text(encoding="utf-8"),
        }
        retired = (
            "config/vsconfig.py",
            "config/vsconfig.json",
            "core/vs_graph.py",
            "core/vs_script.py",
            "自动生成 `.vpy`",
            "主进程预热",
        )
        for name, text in documents.items():
            for token in retired:
                with self.subTest(document=name, token=token):
                    self.assertNotIn(token, text)


if __name__ == "__main__":
    unittest.main()
