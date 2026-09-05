import json
import hashlib
import io
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.vs_runtime.protocol import (
    MAX_MESSAGE_BYTES,
    MessageDecoder,
    ProtocolError,
    encode_message,
)
from core.vs_runtime.script_header import ScriptHeader
from core.vs_runtime.session import (
    NodeMetadata,
    RenderSession,
    ScriptSelection,
    SessionMetadata,
    compute_script_bundle_hash,
    resolve_worker_command,
)
from core.vs_runtime.worker_main import (
    ProtocolWriter,
    _SafeLogSink,
    _install_structured_stdout,
)


class VSWorkerProtocolTests(unittest.TestCase):
    def test_protocol_writer_completes_short_writes_and_rejects_zero_progress(self):
        class ShortStream:
            def __init__(self, *, zero=False):
                self.data = bytearray()
                self.zero = zero
                self.flush_count = 0

            def write(self, chunk):
                if self.zero:
                    return 0
                chunk = bytes(chunk)
                count = max(1, len(chunk) // 2)
                self.data.extend(chunk[:count])
                return count

            def flush(self):
                self.flush_count += 1

        payload = encode_message({"type": "log", "message": "黍" * 200})
        stream = ShortStream()

        ProtocolWriter(stream).send_encoded(payload)

        self.assertEqual(bytes(stream.data), payload)
        self.assertEqual(stream.flush_count, 1)
        with self.assertRaises(OSError):
            ProtocolWriter(ShortStream(zero=True)).send_encoded(payload)

    def test_decoder_handles_split_header_and_coalesced_messages(self):
        first = encode_message({"type": "ready", "api_version": 1})
        second = encode_message({"type": "log", "message": "中文\n日志"})
        decoder = MessageDecoder()

        self.assertEqual(decoder.feed(first[:2]), [])
        self.assertEqual(
            decoder.feed(first[2:] + second),
            [
                {"type": "ready", "api_version": 1},
                {"type": "log", "message": "中文\n日志"},
            ],
        )

    def test_decoder_waits_for_a_split_body(self):
        encoded = encode_message(
            {"type": "request_error", "request_id": 9, "message": "x"}
        )
        decoder = MessageDecoder()

        self.assertEqual(decoder.feed(encoded[:-1]), [])
        self.assertEqual(
            decoder.feed(encoded[-1:]),
            [{"type": "request_error", "request_id": 9, "message": "x"}],
        )

    def test_encode_uses_unsigned_big_endian_utf8_json(self):
        message = {"type": "log", "message": "黍"}

        encoded = encode_message(message)
        length = struct.unpack(">I", encoded[:4])[0]

        self.assertEqual(length, len(encoded) - 4)
        self.assertEqual(json.loads(encoded[4:].decode("utf-8")), message)

    def test_decoder_rejects_zero_oversize_bad_utf8_and_array(self):
        invalid_frames = (
            struct.pack(">I", 0),
            struct.pack(">I", MAX_MESSAGE_BYTES + 1),
            struct.pack(">I", 1) + b"\xff",
            struct.pack(">I", 2) + b"[]",
        )

        for frame in invalid_frames:
            with self.subTest(frame=frame[:8]):
                with self.assertRaises(ProtocolError):
                    MessageDecoder().feed(frame)

    def test_decoder_rejects_surrogate_infinity_and_excessive_nesting(self):
        bodies = (
            b'{"type":"log","message":"\\ud800"}',
            b'{"type":"log","value":1e400}',
            (
                b'{"type":"log","value":'
                + b"[" * 2_000
                + b"0"
                + b"]" * 2_000
                + b"}"
            ),
        )

        for body in bodies:
            with self.subTest(body_prefix=body[:40]):
                frame = struct.pack(">I", len(body)) + body
                with self.assertRaises(ProtocolError):
                    MessageDecoder().feed(frame)

    def test_envelope_requires_string_type_on_encode_and_decode(self):
        invalid_messages = ({}, {"type": None}, {"type": 1})
        for message in invalid_messages:
            with self.subTest(direction="encode", message=message):
                with self.assertRaises(ProtocolError):
                    encode_message(message)
            body = json.dumps(message, separators=(",", ":")).encode("utf-8")
            frame = struct.pack(">I", len(body)) + body
            with self.subTest(direction="decode", message=message):
                with self.assertRaises(ProtocolError):
                    MessageDecoder().feed(frame)

    def test_request_id_is_a_strict_positive_integer_when_present(self):
        invalid_ids = (True, False, 0, -1, 1.0, "1", None)
        for request_id in invalid_ids:
            message = {"type": "ready", "request_id": request_id}
            with self.subTest(direction="encode", request_id=request_id):
                with self.assertRaises(ProtocolError):
                    encode_message(message)
            body = json.dumps(message, separators=(",", ":")).encode("utf-8")
            frame = struct.pack(">I", len(body)) + body
            with self.subTest(direction="decode", request_id=request_id):
                with self.assertRaises(ProtocolError):
                    MessageDecoder().feed(frame)

    def test_encode_rejects_non_object_unencodable_and_oversize_bodies(self):
        invalid_messages = (
            [],
            {"type": "log", "value": object()},
            {"type": "log", "value": float("nan")},
            {"type": "log", "message": "x" * MAX_MESSAGE_BYTES},
        )
        for message in invalid_messages:
            with self.subTest(message_type=type(message).__name__):
                with self.assertRaises(ProtocolError):
                    encode_message(message)

    def test_encode_rejects_shared_container_graph_amplification(self):
        value = [0]
        for _ in range(18):
            value = [value, value]

        with self.assertRaises(ProtocolError) as raised:
            encode_message({"type": "log", "value": value})

        self.assertEqual(raised.exception.code, "protocol.invalid_json")

    def test_feed_requires_a_bytes_like_chunk(self):
        with self.assertRaises(TypeError):
            MessageDecoder().feed("not bytes")

    def test_eof_rejects_a_residual_header_or_body(self):
        clean = MessageDecoder()
        clean.feed(encode_message({"type": "ready"}))
        clean.finish()

        for residual in (b"\0\0", struct.pack(">I", 3) + b"{}"):
            decoder = MessageDecoder()
            decoder.feed(residual)
            with self.subTest(residual=residual):
                with self.assertRaises(ProtocolError):
                    decoder.finish()

    def test_log_chunking_uses_final_json_wire_size(self):
        stream = io.BytesIO()
        sink = _SafeLogSink(ProtocolWriter(stream))
        # 每个引号在 JSON 中需要转义；原始字符数低于 4 MiB，最终正文
        # 却接近 8 MiB，不能按原字符串长度判断。
        original = '"' * (MAX_MESSAGE_BYTES - 1)

        sink(original)

        messages = MessageDecoder().feed(stream.getvalue())
        self.assertGreater(len(messages), 1)
        self.assertEqual("".join(item["message"] for item in messages), original)

    def test_log_sink_failure_never_propagates_into_user_script(self):
        writer = mock.Mock()
        writer.send_encoded.side_effect = BrokenPipeError("closed")

        _SafeLogSink(writer)("用户日志")

        writer.send_encoded.assert_called_once()

    def test_structured_stdout_replaces_lone_surrogate_without_raising(self):
        stream = io.BytesIO()
        original_stdout = sys.stdout
        original_dunder_stdout = sys.__stdout__
        try:
            writer = _install_structured_stdout(ProtocolWriter(stream))
            written = writer.write("before-\ud800-after\n")
            writer.flush()
        finally:
            sys.stdout = original_stdout
            sys.__stdout__ = original_dunder_stdout

        self.assertEqual(written, len("before-\ud800-after\n"))
        messages = MessageDecoder().feed(stream.getvalue())
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["message"], "before-?-after")


def _node_wire(*, editor=False):
    return {
        "width": 384,
        "height": 640,
        "num_frames": 61,
        "fps_num": 30000,
        "fps_den": 1001,
        "pixel_format": "RGB24" if editor else "YUV420P8",
        "matrix": None if editor else "170m",
        "transfer": None if editor else "170m",
        "primaries": None if editor else "170m",
        "range": None if editor else "limited",
    }


class VSWorkerSessionWireTests(unittest.TestCase):
    def test_session_metadata_round_trip_uses_frozen_types(self):
        wire = {
            "epoch": 42,
            "mode": "compatible",
            "capabilities": ["crop", "source", "trim"],
            "output0": _node_wire(),
            "editor": _node_wire(editor=True),
        }

        metadata = SessionMetadata.from_wire(wire)

        self.assertEqual(metadata.epoch, 42)
        self.assertEqual(metadata.capabilities, frozenset({"crop", "source", "trim"}))
        self.assertIsInstance(metadata.output0, NodeMetadata)
        self.assertIsInstance(metadata.editor, NodeMetadata)
        self.assertEqual(metadata.to_wire(), wire)

    def test_metadata_rejects_missing_extra_zero_fps_wrong_mode_and_untyped_colour(self):
        valid = {
            "epoch": 42,
            "mode": "compatible",
            "capabilities": ["source"],
            "output0": _node_wire(),
            "editor": None,
        }
        invalid = []
        missing = dict(valid)
        missing.pop("output0")
        invalid.append(missing)
        extra = dict(valid, extra=True)
        invalid.append(extra)
        zero_fps = dict(valid)
        zero_fps["output0"] = dict(valid["output0"], fps_den=0)
        invalid.append(zero_fps)
        wrong_mode = dict(valid, mode="legacy")
        invalid.append(wrong_mode)
        missing_colour = dict(valid)
        missing_colour["output0"] = dict(valid["output0"], matrix=None)
        invalid.append(missing_colour)

        for wire in invalid:
            with self.subTest(wire=wire):
                with self.assertRaises(ProtocolError):
                    SessionMetadata.from_wire(wire)

    def test_metadata_rejects_float_and_bool_integer_fields(self):
        for field, value in (("epoch", True), ("width", 384.0), ("num_frames", False)):
            wire = {
                "epoch": 1,
                "mode": "raw",
                "capabilities": ["source"],
                "output0": _node_wire(),
                "editor": None,
            }
            if field == "epoch":
                wire[field] = value
            else:
                wire["output0"] = dict(wire["output0"], **{field: value})
            with self.subTest(field=field, value=value):
                with self.assertRaises(ProtocolError):
                    SessionMetadata.from_wire(wire)

    def test_selection_comes_from_header_and_session_builds_exact_load_message(self):
        header = ScriptHeader(
            api_version=1,
            mode="compatible",
            capabilities=("source",),
            requires=(),
            editor_output=0,
        )
        script = Path(__file__).resolve()
        selection = ScriptSelection.from_header(script, header, "a" * 64)
        session = RenderSession(
            epoch=7,
            track="loop",
            selection=selection,
            job_path=str(script),
            job_sha256=hashlib.sha256(script.read_bytes()).hexdigest(),
            runtime_fingerprint="b" * 64,
        )

        self.assertEqual(
            session.to_load_message(13),
            {
                "type": "load",
                "request_id": 13,
                "api_version": 1,
                "track": "loop",
                "epoch": 7,
                "script_path": str(script),
                "job_path": str(script),
                "job_sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
                "bundle_hash": "a" * 64,
                "runtime_fingerprint": "b" * 64,
                "mode": "compatible",
            },
        )

    def test_selection_and_session_reject_invalid_identity_fields(self):
        header = ScriptHeader(1, "raw", ("source",), (), 0)
        script = Path(__file__).resolve()
        with self.assertRaises(ValueError):
            ScriptSelection.from_header(script, header, "not-a-sha256")
        selection = ScriptSelection.from_header(script, header, "a" * 64)
        for kwargs in (
            {"epoch": True, "track": "loop", "job_sha256": "c" * 64, "runtime_fingerprint": "b" * 64},
            {"epoch": 1, "track": "bad", "job_sha256": "c" * 64, "runtime_fingerprint": "b" * 64},
            {"epoch": 1, "track": "loop", "job_sha256": "c" * 64, "runtime_fingerprint": "bad"},
            {"epoch": 1, "track": "loop", "job_sha256": "bad", "runtime_fingerprint": "b" * 64},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    RenderSession(
                        selection=selection,
                        job_path=str(script),
                        **kwargs,
                    )

    def test_resolve_worker_command_is_absolute_for_source_and_frozen_builds(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app_dir = Path(temp_dir)
            with mock.patch.object(sys, "frozen", False, create=True):
                source = resolve_worker_command(app_dir)
            with mock.patch.object(sys, "frozen", True, create=True):
                frozen = resolve_worker_command(app_dir)

        self.assertEqual(Path(source[0]), Path(sys.executable).resolve())
        self.assertEqual(source[1], "-B")
        self.assertEqual(source[2], str((app_dir / "vs_worker.py").resolve()))
        self.assertEqual(frozen, [str((app_dir / "vs_worker.exe").resolve())])

    def test_script_bundle_hash_tracks_python_code_but_not_data_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            script = root / "pipeline.vpy"
            module = root / "modules" / "helper.py"
            data = root / "lookup.json"
            module.parent.mkdir()
            script.write_text("VALUE = 1\n", encoding="utf-8")
            module.write_text("HELPER = 1\n", encoding="utf-8")
            data.write_text('{"value": 1}', encoding="utf-8")

            initial = compute_script_bundle_hash(script)
            data.write_text('{"value": 2}', encoding="utf-8")
            self.assertEqual(compute_script_bundle_hash(script), initial)
            module.write_text("HELPER = 2\n", encoding="utf-8")
            self.assertNotEqual(compute_script_bundle_hash(script), initial)


if __name__ == "__main__":
    unittest.main()
