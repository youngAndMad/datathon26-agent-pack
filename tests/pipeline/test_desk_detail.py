import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch
from cashwatch.dto import Transform
from cashwatch.errors import PipelineError
from cashwatch.pipeline.detail import desk_roi_config, add_desk_crops, crop_context_note
from tests.pipeline.test_verification_frame_selection import frame, candidate


class DeskDetailTests(unittest.TestCase):
    def test_opt_in_and_rule_scope(self):
        self.assertIsNone(desk_roi_config({}, 'CW11'))
        cfg={'enabled':True,'bounds':[.1,.2,.8,.9]}
        self.assertIsNone(desk_roi_config({'desk_roi':cfg}, 'CW12'))
        self.assertEqual(desk_roi_config({'desk_roi':cfg}, 'CW11'),cfg)
        cfg['bounds']=[0,0,float('nan'),1]
        with self.assertRaises(PipelineError):
            desk_roi_config({'desk_roi':cfg}, 'CW11')

    def test_native_request_and_context_preserved(self):
        original=[frame(t) for t in (1,2,3,4,5,6)]
        anchor=original[3]
        media=Mock()
        media.frames_for_times.return_value=[anchor,original[0]]
        media.resolve_frame.return_value=Path('synthetic.jpg')
        meta=SimpleNamespace(width=1920,height=1080)
        def crop(f,*args):
            return f.model_copy(update={'frame_id':'roi-'+f.frame_id,
                                       'transforms':[Transform(kind='crop',parameters={'x':0})]})
        with patch('cashwatch.media.roi.crop_frame',side_effect=crop):
            result=add_desk_crops(media,Path('synthetic.mov'),meta,Path('.'),original,
                                  candidate(anchor.frame_id,[anchor.frame_id]),
                                  {'bounds':[0,0,.5,.5]},2,384,None)
        self.assertEqual(len(result),8)
        self.assertTrue(set(f.frame_id for f in original)<=set(f.frame_id for f in result))
        self.assertEqual(media.frames_for_times.call_args.kwargs['long_side_px'],1920)
        self.assertIn('not evidence of absence',crop_context_note(result))

    def test_timestamp_mismatch_never_silently_used(self):
        f=frame(1)
        media=Mock()
        media.frames_for_times.return_value=[frame(1.001)]
        with self.assertRaises(PipelineError) as caught:
            add_desk_crops(media,Path('synthetic.mov'),SimpleNamespace(width=1920,height=1080),
                           Path('.'),[f],candidate(f.frame_id),{'bounds':[0,0,.5,.5]},1,384,None)
        self.assertEqual(caught.exception.code,'evidence_invalid')


if __name__=='__main__':
    unittest.main()
