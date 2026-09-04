"""Graph parity and requester tests without a VS-bearing Qt parent process."""

from __future__ import annotations

import sys
import unittest
from concurrent.futures import Future
from pathlib import Path

try:
    import cv2

    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

from core.media_tools import MediaToolchain
from tests.helpers.vs_isolation import IsolatedVSCase
from tests.qt_harness import ensure_app


REPO = Path(__file__).resolve().parents[1]
VS_OK = (
    (REPO / "tools" / "media" / "vapoursynth.pyd").is_file()
    and sys.version_info >= (3, 12)
)
TOOLCHAIN = MediaToolchain.discover(str(REPO))
ENCODE_OK = HAS_CV2 and not TOOLCHAIN.missing_for_export()


def setUpModule():
    ensure_app()


class ParentProcessIsolationTests(IsolatedVSCase):
    def test_parent_does_not_import_vapoursynth_or_vs_engine(self):
        self.assert_parent_has_no_vs()


@unittest.skipUnless(
    VS_OK and ENCODE_OK, "VapourSynth / encode toolchain unavailable"
)
class LegacyInProcessGraphTests(IsolatedVSCase):
    def test_programmatic_graph_matches_generated_vpy(self):
        self.assertEqual(self.run_vs_child("graph_contract")["status"], "ok")

    def test_real_clip_frame_requester_contract(self):
        self.assertEqual(
            self.run_vs_child("frame_requester_contract")["status"], "ok"
        )


class FrameRequesterStateTests(IsolatedVSCase):
    """These use fake futures only; keeping them in the Qt parent is intentional."""

    def test_no_clip_means_no_request(self):
        from core.vs_player import FrameRequester

        requester = FrameRequester()
        self.assertFalse(requester.request(0))
        self.assertFalse(requester.has_clip())
        self.assertEqual(requester.num_frames(), 0)
        self.assert_parent_has_no_vs()

    def test_budget_drops_extras_and_keeps_only_the_latest_target(self):
        from core.vs_player import FrameRequester

        pending = []

        class StuckClip:
            num_frames = 200

            def get_frame_async(self, index):
                future = Future()
                pending.append((index, future))
                return future

        requester = FrameRequester()
        requester.set_clip(StuckClip(), epoch=1)
        accepted = [requester.request(index, coalesce=True) for index in range(10)]
        self.assertEqual(accepted.count(True), FrameRequester.MAX_INFLIGHT)
        self.assertEqual(
            accepted[: FrameRequester.MAX_INFLIGHT],
            [True] * FrameRequester.MAX_INFLIGHT,
        )
        self.assertEqual(requester.inflight_count(), FrameRequester.MAX_INFLIGHT)
        self.assertEqual(
            [index for index, _future in pending],
            list(range(FrameRequester.MAX_INFLIGHT)),
        )
        self.assertEqual(requester._latest_wanted, 9)
        pending[0][1].set_result(None)
        self.assertEqual([index for index, _future in pending][-1], 9)
        self.assert_parent_has_no_vs()

    def test_close_silences_callbacks_that_land_after_teardown(self):
        from core.vs_player import FrameRequester

        pending = []

        class StuckClip:
            num_frames = 50

            def get_frame_async(self, _index):
                future = Future()
                pending.append(future)
                return future

        requester = FrameRequester()
        requester.set_clip(StuckClip(), epoch=1)
        events = []
        requester.frame_ready.connect(lambda *_args: events.append("ready"))
        requester.frame_failed.connect(lambda *_args: events.append("failed"))
        self.assertTrue(requester.request(0))
        requester.close()
        pending[0].set_result(None)
        self.assertEqual(events, [])
        self.assertFalse(requester.request(1))
        self.assertEqual(requester.inflight_count(), 0)
        self.assert_parent_has_no_vs()

    def test_clear_drops_the_clip(self):
        from core.vs_player import FrameRequester

        requester = FrameRequester()
        requester.set_clip(object(), epoch=6)
        requester.clear()
        self.assertFalse(requester.has_clip())
        self.assertFalse(requester.request(0))
        self.assert_parent_has_no_vs()


if __name__ == "__main__":
    unittest.main()
