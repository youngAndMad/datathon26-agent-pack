"""Native ROI tests using generated colored rectangles, no external media."""

import hashlib
from pathlib import Path
import shutil
import unittest
import uuid

from PIL import Image

from cashwatch.dto import Transform
from cashwatch.errors import PipelineError
from cashwatch.media.roi import crop_frame
from tests.rules import synth as S


class RoiTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[2] / ".tmp" / ("roi-test-" + uuid.uuid4().hex)
        self.root.mkdir(parents=True)
        self.path = self.root / "native.png"
        image = Image.new("RGB", (800, 400), (255, 0, 0))
        image.paste((0, 255, 0), (400, 0, 800, 400))
        image.save(self.path)
        self.frame = S.frame(2.25).model_copy(update={
            "local_path": "native.png", "width": 800, "height": 400,
            "sha256": hashlib.sha256(self.path.read_bytes()).hexdigest(),
        })

    def tearDown(self):
        expected = Path(__file__).resolve().parents[2] / ".tmp"
        assert self.root.resolve().parent == expected.resolve()
        shutil.rmtree(self.root)

    def crop(self, roi=None, long_side=200):
        return crop_frame(self.frame, self.path, self.root,
                          [0.5, 0, 1, 1] if roi is None else roi, long_side)

    def test_pixels_timeline_provenance_and_original_preserved(self):
        original = self.path.read_bytes()
        cropped = self.crop()
        self.assertEqual((cropped.width, cropped.height), (200, 200))
        for field in ("source_sha256", "ordinal", "pts", "time_base_num", "time_base_den", "relative_sec"):
            self.assertEqual(getattr(cropped, field), getattr(self.frame, field))
        self.assertEqual([t.kind for t in cropped.transforms], ["crop", "resize", "jpeg_encode"])
        output = self.root / cropped.local_path
        self.assertEqual(cropped.sha256, hashlib.sha256(output.read_bytes()).hexdigest())
        with Image.open(output) as image:
            r, g, b = image.getpixel((100, 100))
            self.assertLess(r, 5)
            self.assertGreater(g, 250)
            self.assertLess(b, 5)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.frame.transforms, [])

    def test_stable_identity_and_no_upscale(self):
        a = self.crop(long_side=1000)
        b = self.crop(long_side=1000)
        self.assertEqual(a, b)
        self.assertEqual((a.width, a.height), (400, 400))
        self.assertNotIn("resize", [t.kind for t in a.transforms])
        self.assertNotEqual(a.frame_id, self.crop([0, 0, 0.5, 1], 1000).frame_id)

    def test_invalid_coordinates_and_dimensions(self):
        for roi in ([0, 0, 0, 1], [-1, 0, 1, 1], [0, 0, 2, 1],
                    [0, 0, float("nan"), 1], [0, 1], [False, 0, 1, 1]):
            with self.subTest(roi=roi), self.assertRaises(PipelineError):
                self.crop(roi)
        for side in (0, -1, True, 1.5):
            with self.subTest(side=side), self.assertRaises(PipelineError):
                self.crop(long_side=side)

    def test_hash_and_dimension_mismatch_rejected(self):
        for update in ({"sha256": "f" * 64}, {"width": 799}):
            with self.subTest(update=update), self.assertRaises(PipelineError):
                crop_frame(self.frame.model_copy(update=update), self.path, self.root, [0, 0, 1, 1], 200)

    def test_thumbnail_or_existing_crop_rejected(self):
        for kind in ("resize", "crop"):
            with self.subTest(kind=kind), self.assertRaises(PipelineError):
                crop_frame(self.frame.model_copy(update={"transforms": [Transform(kind=kind)]}),
                           self.path, self.root, [0, 0, 1, 1], 200)

    def test_wrong_image_path_and_path_escape_rejected(self):
        with self.assertRaises(PipelineError):
            crop_frame(self.frame, self.root / "other.png", self.root, [0, 0, 1, 1], 200)
        with self.assertRaises(PipelineError):
            crop_frame(self.frame.model_copy(update={"local_path": "../native.png"}),
                       self.path, self.root, [0, 0, 1, 1], 200)


if __name__ == "__main__":
    unittest.main()
