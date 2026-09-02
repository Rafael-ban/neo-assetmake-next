from __future__ import annotations

import importlib
import json
import subprocess
import sys
import tempfile
import unittest
from collections import namedtuple
from fractions import Fraction
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER_ROOT = ROOT / "resources" / "vapoursynth" / "python"
CHILD = ROOT / "tests" / "helpers" / "run_vs_contract_case.py"


def _contract_module():
    if str(HELPER_ROOT) not in sys.path:
        sys.path.insert(0, str(HELPER_ROOT))
    try:
        return importlib.import_module("assetmaker_vs.contract")
    except ModuleNotFoundError as exc:
        raise AssertionError("portable assetmaker_vs.contract 尚未实现") from exc


class FakeFormat:
    def __init__(self, format_id: int, name: str):
        self.id = format_id
        self.name = name
        self.subsampling_w = 1 if name == "YUV420P8" else 0
        self.subsampling_h = 1 if name == "YUV420P8" else 0


class FakeFrame:
    def __init__(self, node: "FakeVideoNode", props: dict[str, int]):
        self.width = node.width
        self.height = node.height
        self.format = node.format
        self.props = dict(props)

    def close(self) -> None:
        pass


class FakeVideoNode:
    def __init__(
        self,
        *,
        width: int = 384,
        height: int = 640,
        num_frames: int = 5,
        fps: tuple[int, int] = (30000, 1001),
        format_id: int = 100,
        format_name: str = "YUV420P8",
        props_by_frame: list[dict[str, int]] | None = None,
    ):
        self.width = width
        self.height = height
        self.num_frames = num_frames
        self.fps = Fraction(*fps) if fps[1] else Fraction(0, 1)
        self.fps_num = fps[0]
        self.fps_den = fps[1]
        self.format = FakeFormat(format_id, format_name)
        default = {
            "_Matrix": 6,
            "_Transfer": 6,
            "_Primaries": 6,
            "_ColorRange": 1,
        }
        self._props = props_by_frame or [default] * max(0, num_frames)

    def get_frame(self, index: int) -> FakeFrame:
        return FakeFrame(self, self._props[index])


class FakeGuardNode(FakeVideoNode):
    def __init__(self, source: FakeVideoNode, selector):
        self.__dict__.update(source.__dict__)
        self._source = source
        self._selector = selector

    def get_frame(self, index: int) -> FakeFrame:
        return self._selector(index, self._source.get_frame(index))


FakeVideoOutputTuple = namedtuple(
    "VideoOutputTuple", ["clip", "alpha", "alt_output"]
)


class FakeStd:
    @staticmethod
    def ModifyFrame(*, clip, clips, selector):
        if clips is not clip:
            raise AssertionError("guard 必须读取并透传同一 output0 clip")
        return FakeGuardNode(clip, selector)


class FakeCore:
    std = FakeStd()


class FakeVS:
    VideoNode = FakeVideoNode
    VideoOutputTuple = FakeVideoOutputTuple
    YUV420P8 = 100
    core = FakeCore()

    def __init__(self, outputs: dict[int, object]):
        self._outputs = outputs

    def get_output(self, index: int = 0):
        if index not in self._outputs:
            raise RuntimeError(f"output {index} missing")
        return self._outputs[index]


def _job(*, frame_count: int = 5) -> dict[str, object]:
    return {
        "api_version": 1,
        "epoch": 1,
        "track": "loop",
        "project_root": r"D:\素材\黍",
        "source": {
            "path": r"D:\素材\黍\source.mp4",
            "kind": "video",
            "virtual_frame_count": None,
        },
        "timeline": {
            "start_frame": 0,
            "end_frame": frame_count,
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
        "paths": {"cache_dir": r"D:\素材\黍\cache"},
    }


RAW_HEADER = {
    "api_version": 1,
    "mode": "raw",
    "capabilities": ["source"],
    "requires": [],
    "editor_output": 0,
}
COMPATIBLE_HEADER = {
    "api_version": 1,
    "mode": "compatible",
    "capabilities": ["source", "trim"],
    "requires": [],
    "editor_output": 1,
}


def _validated(node: FakeVideoNode, *, header=RAW_HEADER, output1=None):
    contract = _contract_module()
    outputs: dict[int, object] = {
        0: FakeVideoOutputTuple(node, None, 0),
    }
    if output1 is not None:
        outputs[1] = output1
    return contract.validate_outputs(FakeVS(outputs), _job(), header)


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


class OutputContractPureTests(unittest.TestCase):
    def test_valid_170m_limited_maps_to_x264(self):
        contract = _contract_module()

        result = _validated(FakeVideoNode())

        self.assertEqual(
            result.vui,
            contract.X264Vui(
                colormatrix="smpte170m",
                colorprim="smpte170m",
                transfer="smpte170m",
                range_="tv",
            ),
        )

    def test_wrong_format_reports_contract_field(self):
        contract = _contract_module()

        with self.assertRaises(contract.OutputContractError) as raised:
            _validated(
                FakeVideoNode(format_id=101, format_name="YUV422P10")
            )

        self.assertEqual(raised.exception.field, "pixel_format")
        self.assertEqual(raised.exception.expected, "YUV420P8")
        self.assertEqual(raised.exception.actual, "YUV422P10")

    def test_output0_wrapper_contract_is_strict(self):
        contract = _contract_module()
        cases = (
            ({}, "output.0"),
            ({0: object()}, "output.0.type"),
            ({0: FakeVideoOutputTuple(object(), None, 0)}, "output.0.clip"),
            (
                {
                    0: FakeVideoOutputTuple(
                        FakeVideoNode(), FakeVideoNode(), 0
                    )
                },
                "output.0.alpha",
            ),
            ({0: FakeVideoOutputTuple(FakeVideoNode(), None, 1)}, "output.0.alt_output"),
        )
        for outputs, field in cases:
            with self.subTest(field=field):
                with self.assertRaises(contract.OutputContractError) as raised:
                    contract.validate_outputs(FakeVS(outputs), _job(), RAW_HEADER)
                self.assertEqual(raised.exception.field, field)

    def test_static_geometry_timeline_and_rate_are_strict(self):
        contract = _contract_module()
        cases = (
            (FakeVideoNode(width=382), "coded_size"),
            (FakeVideoNode(width=383), "chroma_geometry"),
            (FakeVideoNode(num_frames=0, props_by_frame=[]), "num_frames"),
            (FakeVideoNode(fps=(0, 1)), "fps"),
            (FakeVideoNode(num_frames=4), "num_frames"),
            (FakeVideoNode(fps=(30, 1)), "fps"),
        )
        for node, field in cases:
            with self.subTest(field=field, node=node.__dict__):
                with self.assertRaises(contract.OutputContractError) as raised:
                    _validated(node)
                self.assertEqual(raised.exception.field, field)

    def test_frame_props_are_required_known_and_equal_to_job(self):
        contract = _contract_module()
        valid = {
            "_Matrix": 6,
            "_Transfer": 6,
            "_Primaries": 6,
            "_ColorRange": 1,
        }
        cases = []
        for prop, field in (
            ("_Matrix", "matrix"),
            ("_Transfer", "transfer"),
            ("_Primaries", "primaries"),
            ("_ColorRange", "range"),
        ):
            missing = dict(valid)
            missing.pop(prop)
            cases.append((missing, field))
        unknown = dict(valid, _Matrix=2)
        wrong = dict(valid, _Matrix=1)
        cases.extend(((unknown, "matrix"), (wrong, "matrix")))

        for props, field in cases:
            with self.subTest(props=props):
                with self.assertRaises(contract.OutputContractError) as raised:
                    _validated(FakeVideoNode(props_by_frame=[props] * 5))
                self.assertEqual(raised.exception.field, field)

    def test_range_properties_keep_their_opposite_numbering(self):
        contract = _contract_module()
        base = {"_Matrix": 6, "_Transfer": 6, "_Primaries": 6}
        for props in (
            dict(base, _ColorRange=1),
            dict(base, _Range=0),
            dict(base, _ColorRange=1, _Range=0),
        ):
            with self.subTest(props=props):
                _validated(FakeVideoNode(props_by_frame=[props] * 5))
        for props in (
            dict(base, _ColorRange=0),
            dict(base, _Range=1),
            dict(base, _ColorRange=1, _Range=1),
        ):
            with self.subTest(props=props):
                with self.assertRaises(contract.OutputContractError) as raised:
                    _validated(FakeVideoNode(props_by_frame=[props] * 5))
                self.assertEqual(raised.exception.field, "range")

    def test_compatible_requires_output1_but_raw_does_not_read_it(self):
        contract = _contract_module()
        raw_outputs = {
            0: FakeVideoOutputTuple(FakeVideoNode(), None, 0),
            1: object(),
        }
        contract.validate_outputs(FakeVS(raw_outputs), _job(), RAW_HEADER)

        with self.assertRaises(contract.OutputContractError) as raised:
            contract.validate_outputs(
                FakeVS({0: raw_outputs[0]}), _job(), COMPATIBLE_HEADER
            )
        self.assertEqual(raised.exception.field, "output.1")

        rgb_editor = FakeVideoNode(format_id=200, format_name="RGB24")
        contract.validate_outputs(
            FakeVS(
                {
                    0: raw_outputs[0],
                    1: FakeVideoOutputTuple(rgb_editor, None, 0),
                }
            ),
            _job(),
            COMPATIBLE_HEADER,
        )

    def test_editor_output_covers_resolved_timeline_and_matches_fps(self):
        contract = _contract_module()
        output0 = FakeVideoOutputTuple(FakeVideoNode(), None, 0)
        cases = (
            (FakeVideoNode(num_frames=4), "output.1.num_frames"),
            (FakeVideoNode(fps=(30, 1)), "output.1.fps"),
        )
        for editor, field in cases:
            with self.subTest(field=field):
                with self.assertRaises(contract.OutputContractError) as raised:
                    contract.validate_outputs(
                        FakeVS(
                            {
                                0: output0,
                                1: FakeVideoOutputTuple(editor, None, 0),
                            }
                        ),
                        _job(),
                        COMPATIBLE_HEADER,
                    )
                self.assertEqual(raised.exception.field, field)

    def test_noninteger_frame_props_are_structured_contract_errors(self):
        contract = _contract_module()
        props = {
            "_Matrix": "not-an-integer",
            "_Transfer": 6,
            "_Primaries": 6,
            "_ColorRange": 1,
        }

        with self.assertRaises(contract.OutputContractError) as raised:
            _validated(FakeVideoNode(props_by_frame=[props] * 5))

        self.assertEqual(raised.exception.field, "matrix")

    def test_guard_rejects_non_sentinel_second_frame(self):
        contract = _contract_module()
        good = {
            "_Matrix": 6,
            "_Transfer": 6,
            "_Primaries": 6,
            "_ColorRange": 1,
        }
        drift = dict(good, _Matrix=1)
        node = FakeVideoNode(
            props_by_frame=[good, drift, good, good, good]
        )

        result = _validated(node)
        with self.assertRaises(contract.OutputContractError) as raised:
            result.guarded_clip.get_frame(1)
        self.assertEqual(raised.exception.field, "matrix")

    def test_required_callables_are_checked_before_script_execution(self):
        contract = _contract_module()

        class Namespace:
            Present = staticmethod(lambda: None)
            NotCallable = 42

        class Core:
            demo = Namespace()

        contract.verify_required_callables(Core(), ["demo.Present"])
        for requirement in ("missing.Source", "demo.Missing", "demo.NotCallable"):
            with self.subTest(requirement=requirement):
                with self.assertRaises(contract.RequirementError) as raised:
                    contract.verify_required_callables(Core(), [requirement])
                self.assertEqual(raised.exception.code, "requirement.missing")
                self.assertEqual(raised.exception.field, "requires")
                self.assertEqual(raised.exception.actual, requirement)


class OutputContractRealSubprocessTests(unittest.TestCase):
    def test_real_blank_clip_validates_in_fresh_process(self):
        result = _run_child("contract_valid")

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["format"], "YUV420P8")
        self.assertEqual(payload["matrix"], 6)
        self.assertEqual(payload["vui"]["range"], "tv")

    def test_real_modifyframe_guard_rejects_second_frame(self):
        result = _run_child("contract_late_drift")

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["error"]["field"], "matrix")
        self.assertEqual(payload["error"]["actual"], "709")

    def test_r73_range_probe_pins_color_range_semantics(self):
        result = _run_child("range_probe")

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["limited"].get("_ColorRange"), 1)
        self.assertEqual(payload["full"].get("_ColorRange"), 0)
        self.assertNotIn("_Range", payload["limited"])


if __name__ == "__main__":
    unittest.main()
