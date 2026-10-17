"""Synthetic contract tests runnable with stdlib unittest; no weights or network."""
from contextlib import nullcontext
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from cashwatch.inference.backends.hf_backend import HfBackend
from cashwatch.inference.protocol import GenRequest


def request():
    return GenRequest(system="neutral", segments=[("text", "frame 0 at 1.25s"), ("image", 0)],
                      images=[b"synthetic"], max_tokens=4, max_pixels=65536)


class HfBackendTests(unittest.TestCase):
    def setUp(self):
        b = HfBackend("unused", {"device": "cpu"})
        b.loaded = True
        b.device = SimpleNamespace(type="cpu")
        b._torch = SimpleNamespace(inference_mode=nullcontext)
        b.processor = MagicMock()
        b.processor.image_processor.merge_size = 2
        b.processor.image_token_id = 99
        b.processor.batch_decode.return_value = ['{"observations":[]}']
        self.tokens = MagicMock()
        self.tokens.shape = (1, 5)
        self.tokens.__eq__.return_value.sum.return_value.item.return_value = 2
        row = MagicMock()
        row.prod.return_value.item.return_value = 8
        inputs = MagicMock()
        data = {"input_ids": self.tokens, "image_grid_thw": [row]}
        inputs.__getitem__.side_effect = data.__getitem__
        inputs.get.side_effect = data.get
        inputs.to.return_value = data
        b.processor.return_value = inputs
        b.model = MagicMock()
        b.model.generation_config.eos_token_id = [2, 3]
        self.generated = MagicMock()
        self.generated.shape = (2,)
        self.generated.__getitem__.return_value.item.return_value = 2
        b.model.generate.return_value.__getitem__.return_value = self.generated
        decoder = patch("cashwatch.inference.backends.hf_backend.decode_images",
                        return_value=[SimpleNamespace(size=(32, 64))])
        decoder.start()
        self.addCleanup(decoder.stop)
        self.backend = b

    def test_bounded_generation_actual_counts_and_timestamps(self):
        b = self.backend
        result = b.generate(request())
        self.assertEqual((result.prompt_tokens, result.output_tokens), (5, 2))
        self.assertEqual(result.visual_tokens, [2])
        self.assertEqual(result.visual_tokens_actual_total, 2)
        self.assertEqual(result.image_sizes, [(32, 64)])
        self.assertEqual(result.finish_reason, "stop")
        self.assertIsNone(result.peak_memory_bytes)
        kwargs = b.model.generate.call_args.kwargs
        self.assertIs(kwargs["do_sample"], False)
        self.assertEqual(kwargs["num_beams"], 1)
        self.assertEqual(kwargs["max_new_tokens"], 4)
        self.assertEqual(b.processor.call_args.kwargs["max_pixels"], 65536)
        messages = b.processor.apply_chat_template.call_args.args[0]
        self.assertEqual(messages[1]["content"][0]["text"], "frame 0 at 1.25s")

    def test_truncation(self):
        self.generated.shape = (4,)
        self.generated.__getitem__.return_value.item.return_value = 42
        self.assertEqual(self.backend.generate(request()).finish_reason, "length")

    def test_token_disagreement_rejected_before_generation(self):
        self.tokens.__eq__.return_value.sum.return_value.item.return_value = 7
        with self.assertRaisesRegex(ValueError, "visual token count"):
            self.backend.generate(request())
        self.backend.model.generate.assert_not_called()

    def test_oom_propagates_without_retry(self):
        self.backend.model.generate.side_effect = RuntimeError("CUDA out of memory")
        with self.assertRaisesRegex(RuntimeError, "out of memory"):
            self.backend.generate(request())
        self.assertEqual(self.backend.model.generate.call_count, 1)

    def test_bad_image_references(self):
        for indices in [[-1], [1], [0, 0], [True], []]:
            with self.subTest(indices=indices):
                req = request()
                req.segments = [("image", i) for i in indices]
                with self.assertRaisesRegex(ValueError, "original order"):
                    self.backend.generate(req)
        self.backend.model.generate.assert_not_called()

    def test_unloaded(self):
        with self.assertRaisesRegex(RuntimeError, "not loaded"):
            HfBackend("unused", {}).generate(request())

    def test_bad_token_budgets(self):
        for budget in [0, -1, 32769, 1.5]:
            with self.subTest(budget=budget):
                req = request()
                req.max_tokens = budget
                with self.assertRaisesRegex(ValueError, "max_tokens"):
                    self.backend.generate(req)

    def test_local_resident_load(self):
        path = Path(__file__).resolve().parent / "synthetic-model"
        with patch("cashwatch.inference.backends.hf_backend.check_model_dir", return_value=path):
            torch = MagicMock()
            torch.__version__ = "synthetic"
            torch.device.return_value = SimpleNamespace(type="cpu")
            transformers = MagicMock()
            transformers.__version__ = "synthetic"
            with patch.dict(sys.modules, {"torch": torch, "transformers": transformers}), patch.dict(os.environ):
                b = HfBackend(str(path), {"device": "auto"})
                b.load()
                b.load()
                torch.device.assert_called_once_with("cpu")
                for loader in [transformers.AutoProcessor.from_pretrained,
                               transformers.Qwen3VLForConditionalGeneration.from_pretrained]:
                    self.assertEqual(loader.call_count, 1)
                    self.assertEqual(loader.call_args.args, (str(path.resolve()),))
                    self.assertIs(loader.call_args.kwargs["local_files_only"], True)
                    self.assertIs(loader.call_args.kwargs["trust_remote_code"], False)
                self.assertEqual(os.environ["HF_HUB_OFFLINE"], "1")
                self.assertEqual(transformers.Qwen3VLForConditionalGeneration.from_pretrained.call_args.kwargs["dtype"], torch.float32)

    def test_missing_weights(self):
        with self.assertRaises(FileNotFoundError):
            HfBackend(str(Path(__file__).resolve().parent), {}).load()


if __name__ == "__main__":
    unittest.main()
