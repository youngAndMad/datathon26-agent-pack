"""Dense targets preserve context, source anchors and the model memory cap."""
import unittest
from types import SimpleNamespace
from tests.pipeline.test_verification_frame_selection import candidate, frame
from cashwatch.pipeline.planner import anchored_refinement_times, select_verification_frames


class AnchorRefinementTests(unittest.TestCase):
    def setUp(self):
        self.sampling = {'refinement_radius_sec':3, 'refinement_fps':1,
                         'max_refinement_frames':8,
                         'anchor_refinement':{'enabled':True,'radius_sec':2,'fps':4}}
        self.meta = SimpleNamespace(first_frame_sec=0, last_frame_sec=20)

    def test_dense_targets_include_anchor_and_wide_context(self):
        f = frame(5)
        c = candidate(f.frame_id, [f.frame_id])
        times = anchored_refinement_times(c, [f], self.sampling, self.meta)
        self.assertTrue(set(3+i/4 for i in range(17)) <= set(times))
        self.assertEqual((times[0], times[-1]), (0, 13))

    def test_unknown_or_missing_anchor_does_not_invent_focus(self):
        f = frame(5)
        for c in (candidate(), candidate(f.frame_id, [f.frame_id], state='unknown')):
            self.assertEqual(len(anchored_refinement_times(c, [f], self.sampling, self.meta)), 8)

    def test_small_gpu_keeps_context_and_caps_model_frames(self):
        f = frame(5)
        c = candidate(f.frame_id, [f.frame_id])
        times = anchored_refinement_times(c, [f], self.sampling, self.meta)
        selected = select_verification_frames(c, [f], [frame(t) for t in times], 8, focus_sec=5)
        self.assertEqual(len(selected), 8)
        self.assertEqual((selected[0].relative_sec, selected[-1].relative_sec), (0, 13))
        self.assertIn(f.frame_id, [x.frame_id for x in selected])
        self.assertTrue(any(0 < abs(x.relative_sec-5) <= .25 for x in selected))

    def test_clip_boundary_and_invalid_density(self):
        f = frame(0)
        c = candidate(f.frame_id, [f.frame_id])
        self.assertGreaterEqual(min(anchored_refinement_times(c, [f], self.sampling, self.meta)), 0)
        self.sampling['anchor_refinement']['fps'] = 100
        with self.assertRaises(ValueError):
            anchored_refinement_times(c, [f], self.sampling, self.meta)


if __name__ == '__main__':
    unittest.main()
