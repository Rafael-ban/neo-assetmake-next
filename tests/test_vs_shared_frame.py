import mmap
import unittest
import uuid
from unittest import mock

import numpy as np

from core.vs_runtime.shared_frame import (
    MAX_FRAME_BYTES,
    FrameSlot,
    FrameSlotDescriptor,
    checked_frame_bytes,
)


class _PaddedRGBFrame:
    def __init__(self):
        self.width = 3
        self.height = 2
        self.format = type("Format", (), {"name": "RGB24", "num_planes": 3})()
        self._planes = (
            bytes((10, 20, 30, 201, 202, 40, 50, 60, 203, 204)),
            bytes((70, 80, 90, 205, 206, 100, 110, 120, 207, 208)),
            bytes((130, 140, 150, 209, 210, 160, 170, 180, 211, 212)),
        )

    def __getitem__(self, plane):
        return memoryview(self._planes[plane])

    def get_stride(self, plane):
        del plane
        return 5


class VSSharedFrameTests(unittest.TestCase):
    def test_checked_frame_bytes_validates_strict_positive_inputs_and_limit(self):
        self.assertEqual(checked_frame_bytes(480, 854), 1_229_760)

        invalid = (
            (True, 10, 3),
            (10.0, 10, 3),
            (0, 10, 3),
            (10, -1, 3),
            (10, 10, 0),
        )
        for width, height, channels in invalid:
            with self.subTest(values=(width, height, channels)):
                with self.assertRaises(ValueError):
                    checked_frame_bytes(width, height, channels)

        with self.assertRaises(ValueError):
            checked_frame_bytes(MAX_FRAME_BYTES, 2, 3)

    def test_owner_and_peer_share_packed_bgr_and_owner_reads_a_copy(self):
        capacity = checked_frame_bytes(7, 5)
        owner = FrameSlot.create(capacity=capacity, generation=9)
        self.addCleanup(owner.close)
        peer = FrameSlot.open(owner.descriptor)
        self.addCleanup(peer.close)
        expected = np.arange(capacity, dtype=np.uint8).reshape(5, 7, 3)

        peer.write_bgr(expected)
        actual = owner.read_bgr(width=7, height=5, byte_count=expected.nbytes)

        np.testing.assert_array_equal(actual, expected)
        self.assertTrue(actual.flags["C_CONTIGUOUS"])
        self.assertTrue(actual.flags["OWNDATA"])
        peer.write_bgr(np.zeros_like(expected))
        np.testing.assert_array_equal(actual, expected)

    def test_read_uses_mapping_buffer_without_intermediate_bytes_copy(self):
        mapping = mock.MagicMock()
        mapping.closed = False
        mapping.__getitem__.side_effect = AssertionError(
            "mmap slicing creates an avoidable bytes copy"
        )
        metadata = mock.MagicMock()
        metadata.closed = False
        slot = FrameSlot(
            FrameSlotDescriptor("test-slot", 1, 12),
            mapping,
            metadata,
            owner=True,
        )
        flat = np.arange(12, dtype=np.uint8)

        with mock.patch(
            "core.vs_runtime.shared_frame.np.frombuffer",
            return_value=flat,
        ) as frombuffer:
            actual = slot.read_bgr(width=2, height=2, byte_count=12)

        frombuffer.assert_called_once_with(
            mapping, dtype=np.uint8, count=12
        )
        self.assertTrue(actual.flags["OWNDATA"])
        np.testing.assert_array_equal(actual.reshape(-1), flat)

    def test_descriptor_round_trip_is_strict(self):
        descriptor = FrameSlotDescriptor(
            name=r"Local\AssetMaker-test", generation=4, capacity=99
        )
        self.assertEqual(
            FrameSlotDescriptor.from_wire(descriptor.to_wire()), descriptor
        )
        invalid = (
            {},
            {"name": "x", "generation": 1, "capacity": 1, "extra": 2},
            {"name": "", "generation": 1, "capacity": 1},
            {"name": "x", "generation": True, "capacity": 1},
            {"name": "x", "generation": 0, "capacity": 1},
            {"name": "x", "generation": 1, "capacity": 0},
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    FrameSlotDescriptor.from_wire(value)

    def test_rgb_planes_are_copied_by_stride_and_reordered_to_bgr(self):
        owner = FrameSlot.create(capacity=checked_frame_bytes(3, 2), generation=1)
        self.addCleanup(owner.close)
        peer = FrameSlot.open(owner.descriptor)
        self.addCleanup(peer.close)

        byte_count = peer.write_vs_rgb(_PaddedRGBFrame())
        actual = owner.read_bgr(width=3, height=2, byte_count=byte_count)

        expected = np.array(
            [
                [[130, 70, 10], [140, 80, 20], [150, 90, 30]],
                [[160, 100, 40], [170, 110, 50], [180, 120, 60]],
            ],
            dtype=np.uint8,
        )
        np.testing.assert_array_equal(actual, expected)

    def test_capacity_and_byte_count_mismatches_are_rejected(self):
        owner = FrameSlot.create(capacity=8, generation=1)
        self.addCleanup(owner.close)
        peer = FrameSlot.open(owner.descriptor)
        self.addCleanup(peer.close)

        with self.assertRaises(ValueError):
            peer.write_bgr(np.zeros((2, 2, 3), dtype=np.uint8))
        with self.assertRaises(ValueError):
            owner.read_bgr(width=1, height=1, byte_count=2)
        with self.assertRaises(ValueError):
            owner.read_bgr(width=2, height=2, byte_count=12)

    def test_slot_close_is_idempotent_and_closes_mapping(self):
        slot = FrameSlot.create(capacity=3, generation=1)
        mapping = slot.mapping

        slot.close()
        slot.close()

        self.assertTrue(mapping.closed)
        with self.assertRaises(ValueError):
            slot.write_bgr(np.zeros((1, 1, 3), dtype=np.uint8))

    def test_peer_open_rejects_a_missing_mapping_instead_of_creating_it(self):
        descriptor = FrameSlotDescriptor(
            name=f"Local\\AssetMaker-missing-{uuid.uuid4().hex}",
            generation=1,
            capacity=16,
        )

        with self.assertRaises(FileNotFoundError):
            FrameSlot.open(descriptor)

    def test_peer_open_rejects_descriptor_capacity_or_generation_mismatch(self):
        owner = FrameSlot.create(capacity=16, generation=7)
        self.addCleanup(owner.close)

        for descriptor in (
            FrameSlotDescriptor(owner.descriptor.name, 7, 8),
            FrameSlotDescriptor(owner.descriptor.name, 8, 16),
        ):
            with self.subTest(descriptor=descriptor):
                with self.assertRaises(ValueError):
                    FrameSlot.open(descriptor)


if __name__ == "__main__":
    if mmap.PAGESIZE <= 0:
        raise RuntimeError("invalid mmap page size")
    unittest.main()
