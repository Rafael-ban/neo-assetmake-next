from __future__ import annotations

import copy
import unittest
from types import SimpleNamespace

from tests.test_vs_output_contract import (
    FakeRange, FakeStd, FakeVideoNode, FakeVideoOutputTuple, RAW_HEADER,
    _contract_module, _job,
)


class BoundaryNode(FakeVideoNode):
    def __init__(self, *, family=2, name="RGBS", format_id=200, **kwargs):
        super().__init__(format_id=format_id, format_name=name, **kwargs)
        self.format.color_family = family


class BoundaryCore:
    def __init__(self):
        self.calls = []
        self.resize = SimpleNamespace(Lanczos=self.convert)
        self.std = SimpleNamespace(
            ModifyFrame=FakeStd.ModifyFrame, AddBorders=self.borders,
            Turn180=self.rotate, SetFrameProps=self.props,
        )

    def convert(self, clip, **kwargs):
        self.calls.append(("convert", kwargs))
        # Force consumption of source guards before conversion.
        for index in range(clip.num_frames):
            clip.get_frame(index).close()
        return BoundaryNode(
            family=1, name="YUV420P8", format_id=100,
            width=clip.width, height=clip.height, num_frames=clip.num_frames,
            fps=(clip.fps_num, clip.fps_den),
        )

    def borders(self, clip, *, right, bottom):
        self.calls.append(("borders", (right, bottom)))
        result = copy.copy(clip)
        result.width += right
        result.height += bottom
        return result

    def rotate(self, clip):
        self.calls.append(("rotate", None))
        return clip

    def props(self, clip, **props):
        self.calls.append(("props", props))
        result = copy.copy(clip)
        result._props = [dict(props) for _ in range(clip.num_frames)]
        return result


class BoundaryVS:
    RGB = 2
    YUV = 1
    GRAY = 3
    YUV420P8 = 100
    Range = FakeRange
    VideoNode = FakeVideoNode
    VideoOutputTuple = FakeVideoOutputTuple

    def __init__(self, clip):
        self.core = BoundaryCore()
        self.clip = clip

    def get_output(self, index):
        assert index == 0
        return FakeVideoOutputTuple(self.clip, None, 0)


def rgb_node(**kwargs):
    props = {"_Matrix": 0, "_Transfer": 1, "_Primaries": 1, "_Range": 1}
    return BoundaryNode(props_by_frame=[dict(props) for _ in range(5)], **kwargs)


class EncodingBoundaryTests(unittest.TestCase):
    def test_rgb_float_preview_retains_pixels_and_actual_colour(self):
        contract = _contract_module()
        node = rgb_node()
        vs = BoundaryVS(node)
        result = contract.validate_script_outputs(vs, _job(), RAW_HEADER)
        self.assertIs(result.clip, node)
        self.assertEqual(result.guarded_clip.format.name, "RGBS")
        self.assertEqual(result.colour, {
            "matrix": "0", "transfer": "709", "primaries": "709", "range": "full",
        })
        self.assertEqual(vs.core.calls, [])

    def test_export_converts_rgb_then_pads_rotates_and_derives_vui(self):
        contract = _contract_module()
        vs = BoundaryVS(rgb_node())
        job = _job()
        job["output"].update(coded_width=368, coded_height=648, final_rotate_180=True)
        result = contract.validate_encoding_outputs(vs, job, RAW_HEADER)
        self.assertEqual(result.guarded_clip.format.name, "YUV420P8")
        self.assertEqual((result.clip.width, result.clip.height), (368, 648))
        self.assertEqual(result.vui.to_dict(), {
            "colormatrix": "smpte170m", "colorprim": "smpte170m",
            "transfer": "smpte170m", "range": "tv",
        })
        self.assertEqual([name for name, _ in vs.core.calls],
                         ["convert", "borders", "rotate", "props"])
        conversion = vs.core.calls[0][1]
        self.assertEqual(conversion["transfer_s"], "601")
        self.assertEqual(conversion["dither_type"], "error_diffusion")
        self.assertNotIn("width", conversion)

    def test_late_source_colour_change_is_checked_before_encoding(self):
        contract = _contract_module()
        node = rgb_node()
        node._props[1]["_Transfer"] = 6
        with self.assertRaises(contract.OutputContractError) as caught:
            contract.validate_encoding_outputs(BoundaryVS(node), _job(), RAW_HEADER)
        self.assertEqual(caught.exception.field, "frame_props")

    def test_source_size_is_display_size_and_variable_format_is_rejected(self):
        contract = _contract_module()
        for node in (rgb_node(width=368), rgb_node()):
            if node.width == 360:
                node.format = None
            with self.subTest(width=node.width, format=node.format):
                with self.assertRaises(contract.OutputContractError):
                    contract.validate_script_outputs(BoundaryVS(node), _job(), RAW_HEADER)

    def test_gray_source_does_not_require_a_yuv_matrix(self):
        contract = _contract_module()
        node = BoundaryNode(family=3, name="GRAYS", props_by_frame=[
            {"_Transfer": 1, "_Primaries": 1, "_Range": 1} for _ in range(5)
        ])
        result = contract.validate_script_outputs(BoundaryVS(node), _job(), RAW_HEADER)
        self.assertEqual(result.colour["matrix"], "gray")
        self.assertEqual(result.guarded_clip.format.name, "GRAYS")

    def test_source_missing_colour_is_not_relabelled_as_device(self):
        contract = _contract_module()
        node = rgb_node()
        del node._props[0]["_Transfer"]
        with self.assertRaises(contract.OutputContractError) as caught:
            contract.validate_script_outputs(BoundaryVS(node), _job(), RAW_HEADER)
        self.assertEqual(caught.exception.field, "transfer")


if __name__ == "__main__":
    unittest.main()
