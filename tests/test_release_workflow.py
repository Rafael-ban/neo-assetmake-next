import unittest
from pathlib import Path


class ReleaseWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = Path(".github/workflows/release.yml").read_text(
            encoding="utf-8"
        )
        self.build_workflow = Path(".github/workflows/build.yml").read_text(
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

    def test_release_build_does_not_require_pyarmor_license(self):
        self.assertIn("      obfuscate: false", self.workflow)
        self.assertNotIn("      obfuscate: true", self.workflow)

    def test_manual_build_does_not_offer_unlicensed_obfuscation(self):
        self.assertIn("      obfuscate: false", self.build_workflow)
        self.assertNotIn("inputs.obfuscate", self.build_workflow)


if __name__ == "__main__":
    unittest.main()
