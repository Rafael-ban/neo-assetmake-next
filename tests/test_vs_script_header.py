import tempfile
import unittest
from pathlib import Path

from core.vs_runtime.script_header import (
    HEADER_LIMIT_BYTES,
    ScriptHeaderError,
    parse_script_header,
    parse_script_header_text,
)


VALID_HEADER = """# assetmaker-api: 1
# assetmaker-mode: compatible
# assetmaker-capabilities: source,trim,crop,rotation,resolution,image_loop
# assetmaker-requires: lsmas.LWLibavSource,imwri.Read
# assetmaker-editor-output: 1
"""


class ScriptHeaderTests(unittest.TestCase):
    def test_parses_declared_header(self):
        header = parse_script_header_text(
            VALID_HEADER + "import vapoursynth as vs\n"
        )

        self.assertEqual(header.api_version, 1)
        self.assertEqual(header.mode, "compatible")
        self.assertEqual(header.editor_output, 1)
        self.assertEqual(
            header.capabilities,
            ("source", "trim", "crop", "rotation", "resolution", "image_loop"),
        )
        self.assertEqual(
            header.requires,
            ("lsmas.LWLibavSource", "imwri.Read"),
        )

    def test_first_python_statement_stops_header_scan(self):
        text = (
            VALID_HEADER
            + "import vapoursynth as vs\n"
            + "# assetmaker-api: 999\n"
        )

        header = parse_script_header_text(text)

        self.assertEqual(header.api_version, 1)

    def test_header_after_first_statement_is_not_accepted(self):
        with self.assertRaisesRegex(ScriptHeaderError, "缺少"):
            parse_script_header_text("import os\n" + VALID_HEADER)

    def test_invalid_scalar_fields_are_rejected(self):
        replacements = (
            ("assetmaker-api: 1", "assetmaker-api: 2"),
            ("assetmaker-mode: compatible", "assetmaker-mode: magic"),
            ("assetmaker-editor-output: 1", "assetmaker-editor-output: 2"),
        )
        for old, new in replacements:
            with self.subTest(replacement=new):
                with self.assertRaises(ScriptHeaderError):
                    parse_script_header_text(VALID_HEADER.replace(old, new))

    def test_unknown_capability_is_rejected(self):
        invalid = VALID_HEADER.replace(
            "source,trim,crop,rotation,resolution,image_loop",
            "source,trim,network",
        )

        with self.assertRaisesRegex(ScriptHeaderError, "capability"):
            parse_script_header_text(invalid)

    def test_requirements_must_be_namespace_function(self):
        for requirement in (
            "lsmas",
            "lsmas.deep.Function",
            "lsmas.LWLibavSource()",
            "9lsmas.Source",
        ):
            with self.subTest(requirement=requirement):
                invalid = VALID_HEADER.replace(
                    "lsmas.LWLibavSource,imwri.Read", requirement
                )
                with self.assertRaisesRegex(ScriptHeaderError, "requirement"):
                    parse_script_header_text(invalid)

    def test_compatible_editor_operations_require_editor_output(self):
        invalid = VALID_HEADER.replace(
            "assetmaker-editor-output: 1", "assetmaker-editor-output: 0"
        )

        with self.assertRaisesRegex(ScriptHeaderError, "editor-output"):
            parse_script_header_text(invalid)

        source_only = invalid.replace(
            "source,trim,crop,rotation,resolution,image_loop", "source"
        )
        self.assertEqual(parse_script_header_text(source_only).editor_output, 0)

    def test_raw_mode_does_not_consume_editor_output(self):
        raw = VALID_HEADER.replace(
            "assetmaker-mode: compatible", "assetmaker-mode: raw"
        ).replace("assetmaker-editor-output: 1", "assetmaker-editor-output: 0")

        header = parse_script_header_text(raw)

        self.assertEqual(header.mode, "raw")
        self.assertEqual(header.editor_output, 0)
        with self.assertRaisesRegex(ScriptHeaderError, "raw"):
            parse_script_header_text(
                raw.replace(
                    "assetmaker-editor-output: 0",
                    "assetmaker-editor-output: 1",
                )
            )

    def test_parse_file_reads_no_more_than_first_8_kib(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "late-header.vpy"
            prefix = "#" + (" " * 8_190) + "\n"
            path.write_text(prefix + VALID_HEADER, encoding="utf-8")

            with self.assertRaises(ScriptHeaderError):
                parse_script_header(path)

    def test_complete_declaration_ending_at_8_kib_is_accepted(self):
        base = VALID_HEADER.replace(
            "# assetmaker-editor-output: 1\n", ""
        ).encode("utf-8")
        final_line = b"# assetmaker-editor-output: 1\n"
        padding_size = HEADER_LIMIT_BYTES - len(base) - len(final_line)
        padding = b"#" + (b" " * (padding_size - 2)) + b"\n"
        payload = base + padding + final_line + b"import os\n"
        self.assertEqual(len(payload[:HEADER_LIMIT_BYTES]), HEADER_LIMIT_BYTES)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "boundary.vpy"
            path.write_bytes(payload)
            self.assertEqual(parse_script_header(path).editor_output, 1)
        self.assertEqual(
            parse_script_header_text(payload.decode("utf-8")).editor_output,
            1,
        )

    def test_value_suffix_crossing_8_kib_is_not_parsed_as_truncated_value(self):
        base = VALID_HEADER.replace(
            "# assetmaker-editor-output: 1\n", ""
        ).encode("utf-8")
        partial = b"# assetmaker-editor-output: 1"
        padding_size = HEADER_LIMIT_BYTES - len(base) - len(partial)
        padding = b"#" + (b" " * (padding_size - 2)) + b"\n"
        payload = base + padding + partial + b"evil\n"

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "partial-value.vpy"
            path.write_bytes(payload)
            with self.assertRaises(ScriptHeaderError):
                parse_script_header(path)
        with self.assertRaises(ScriptHeaderError):
            parse_script_header_text(payload.decode("utf-8"))

    def test_utf8_character_crossing_8_kib_discards_only_partial_comment(self):
        base = VALID_HEADER.encode("utf-8")
        ascii_size = HEADER_LIMIT_BYTES - len(base) - 1
        partial_comment = b"#" + (b" " * (ascii_size - 1))
        payload = base + partial_comment + "黍".encode("utf-8") + b"\n"
        self.assertEqual(payload[HEADER_LIMIT_BYTES - 1], "黍".encode("utf-8")[0])

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "utf8-boundary.vpy"
            path.write_bytes(payload)
            self.assertEqual(parse_script_header(path).api_version, 1)
        self.assertEqual(
            parse_script_header_text(payload.decode("utf-8")).api_version,
            1,
        )

    def test_parse_file_error_contains_absolute_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = (Path(temp_dir) / "invalid.vpy").resolve()
            path.write_text("import os\n", encoding="utf-8")

            with self.assertRaises(ScriptHeaderError) as raised:
                parse_script_header(path)

            self.assertIn(str(path), str(raised.exception))


if __name__ == "__main__":
    unittest.main()
