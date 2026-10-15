import unittest
from pathlib import Path


class ReleaseWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = Path(".github/workflows/release.yml").read_text(
            encoding="utf-8"
        )

    def test_changelog_extractor_accepts_preview_heading_suffix(self):
        self.assertIn("VERSION_HEADER_RE", self.workflow)
        self.assertNotIn("/^### v${VERSION_ESC}$/", self.workflow)

    def test_preview_release_is_marked_as_prerelease(self):
        self.assertIn("PRERELEASE=", self.workflow)
        self.assertIn(
            "prerelease: ${{ steps.changelog.outputs.PRERELEASE }}",
            self.workflow,
        )


if __name__ == "__main__":
    unittest.main()
