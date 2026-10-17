"""Frame-selection regression with synthetic metadata only."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from cashwatch.dto import CandidateEvent, FrameRef, PredicateAssessment
from cashwatch.errors import PipelineError
from cashwatch.pipeline.planner import select_verification_frames


def frame(t, width=480):
    pts = round(t * 1000)
    return FrameRef(frame_id=f"p{pts}", source_sha256="0" * 64, ordinal=pts,
        pts=pts, time_base_num=1, time_base_den=1000, relative_sec=t,
        local_path=f"frames/p{pts}-{width}.jpg", sha256=("1" if width == 480 else "2") * 64,
        width=width, height=100)


def candidate(anchor=None, supporting=(), contradicting=(), state="supported"):
    return CandidateEvent(candidate_id="c1", rule_id="R1", rule_version="1", start_sec=0,
        end_sec=10, anchor_frame_id=anchor,
        assessments=[PredicateAssessment(predicate_id="R1.action", state=state,
            supporting_frame_ids=list(supporting), contradicting_frame_ids=list(contradicting))])


class SelectionTests(unittest.TestCase):
    def test_brief_anchor_preserved_with_exact_pts_and_endpoints(self):
        anchor = frame(4.137)
        refined = [frame(t, 384) for t in range(11)]
        result = select_verification_frames(candidate(anchor.frame_id, [anchor.frame_id]),
                                            [anchor], refined, 5)
        self.assertEqual(len(result), 5)
        self.assertIn(anchor, result)
        self.assertEqual((result[0].relative_sec, result[-1].relative_sec), (0, 10))
        self.assertEqual([f.relative_sec for f in result], sorted(f.relative_sec for f in result))

    def test_supporting_and_contradicting_context_survives(self):
        before, after = frame(1.137), frame(2.137)
        result = select_verification_frames(candidate(after.frame_id, [after.frame_id], [before.frame_id]),
                                            [before, after], [frame(0), frame(3)], 4)
        self.assertEqual({f.frame_id for f in result}, {f.frame_id for f in (before, after, frame(0), frame(3))})

    def test_refined_same_pts_thumbnail_preferred(self):
        old, new = frame(1), frame(1, 384)
        result = select_verification_frames(candidate(old.frame_id), [old], [new], 1)
        self.assertEqual(result, [new])

    def test_over_budget_protected_frames_are_thinned_not_failed(self):
        # Real clips: a coarse cue spanning a 13-16 frame window exceeds the verification budget;
        # failing usable footage is worse than an even temporal spread of the cited frames.
        frames = [frame(t) for t in range(3)]
        result = select_verification_frames(candidate(None, [f.frame_id for f in frames]), frames, [], 2)
        self.assertEqual([f.frame_id for f in result], [frames[0].frame_id, frames[2].frame_id])

    def test_missing_protected_frame_fails(self):
        with self.assertRaises(PipelineError) as caught:
            select_verification_frames(candidate("missing"), [], [frame(1)], 3)
        self.assertEqual(caught.exception.code, "evidence_invalid")

    def test_unknown_assessment_does_not_require_uncited_frame(self):
        result = select_verification_frames(candidate(None, ["unknown-id"], state="unknown"), [], [frame(1)], 2)
        self.assertEqual(len(result), 1)

    def test_same_id_inconsistent_timeline_fails(self):
        old = frame(1)
        new = old.model_copy(update={"pts": 999})
        with self.assertRaises(PipelineError):
            select_verification_frames(candidate(old.frame_id), [old], [new], 2)

    def test_mixed_sources_fail(self):
        a, b = frame(1), frame(2).model_copy(update={"source_sha256": "9" * 64})
        with self.assertRaises(PipelineError):
            select_verification_frames(candidate(a.frame_id), [a], [b], 2)

    def test_unprotected_small_refinement_unchanged(self):
        frames = [frame(t) for t in range(3)]
        self.assertEqual(select_verification_frames(candidate(), [], frames, 16), frames)


if __name__ == "__main__":
    unittest.main()
