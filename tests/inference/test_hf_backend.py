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
        b.processor.image_processor.size = {"shortest_edge": 65536, "longest_edge": 1000000}
        b.image_token_id = 99
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
        b.processor.apply_chat_template.return_value = inputs
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
        self.assertEqual(b.processor.image_processor.size["longest_edge"], 1000000)
        self.assertIs(b.processor.apply_chat_template.call_args.kwargs["tokenize"], True)
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
        for budget in [0, -1, 32769, 1.5, True]:
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
            with patch.dict(sys.modules, {"torch": torch, "transformers": transformers}), patch.dict(os.environ), patch("sys.platform", "win32"):
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

    def test_auto_device_preserves_unix_accelerators(self):
        torch = MagicMock()
        b = HfBackend("unused", {})
        for platform, cuda, mps, expected in [
            ("win32", True, False, "cpu"),
            ("linux", True, False, "cuda"),
            ("darwin", False, True, "mps"),
            ("linux", False, False, "cpu"),
        ]:
            with self.subTest(platform=platform, expected=expected), patch("sys.platform", platform):
                torch.cuda.is_available.return_value = cuda
                torch.backends.mps.is_available.return_value = mps
                self.assertEqual(b._device(torch), expected)
        self.assertEqual(HfBackend("unused", {"device": "cuda:1"})._device(torch), "cuda:1")

    def test_explicit_accelerator_load_and_unavailable_failure(self):
        path = Path(__file__).resolve().parent / "synthetic-model"
        for platform, device, dtype in [("win32", "cuda", "float16"),
                                         ("darwin", "mps", "bfloat16"),
                                         ("linux", "cuda", "bfloat16")]:
            with self.subTest(platform=platform, device=device):
                torch, transformers = MagicMock(), MagicMock()
                torch.__version__ = transformers.__version__ = "synthetic"
                torch.device.return_value = SimpleNamespace(type=device)
                torch.cuda.is_available.return_value = True
                torch.backends.mps.is_available.return_value = True
                with patch("cashwatch.inference.backends.hf_backend.check_model_dir", return_value=path), \
                     patch.dict(sys.modules, {"torch": torch, "transformers": transformers}), \
                     patch.dict(os.environ), patch("sys.platform", platform):
                    HfBackend(str(path), {"device": device}).load()
                    loader = transformers.Qwen3VLForConditionalGeneration.from_pretrained
                    self.assertEqual(loader.call_args.kwargs["dtype"], getattr(torch, dtype))
                    loader.reset_mock()
                    torch.cuda.is_available.return_value = False
                    torch.backends.mps.is_available.return_value = False
                    with self.assertRaisesRegex(RuntimeError, "unavailable"):
                        HfBackend(str(path), {"device": device}).load()
                    loader.assert_not_called()

    def test_accelerator_sync_and_memory(self):
        for device in ["mps", "cuda"]:
            with self.subTest(device=device):
                b = self.backend
                b.device = SimpleNamespace(type=device)
                b._torch = MagicMock()
                b._torch.inference_mode = nullcontext
                b._torch.mps.driver_allocated_memory.return_value = 1234
                b._torch.cuda.max_memory_allocated.return_value = 1234
                result = b.generate(request())
                self.assertEqual(result.peak_memory_bytes, 1234)
                self.assertEqual(getattr(b._torch, device).synchronize.call_count, 2)

    def test_processor_size_restored_on_failure(self):
        b = self.backend
        sizes = []
        def fail(*args, **kwargs):
            sizes.append(dict(b.processor.image_processor.size))
            raise ValueError("bad input")
        b.processor.apply_chat_template.side_effect = fail
        with self.assertRaisesRegex(ValueError, "bad input"):
            b.generate(request())
        self.assertEqual(sizes[0]["longest_edge"], 65536)
        self.assertEqual(b.processor.image_processor.size["longest_edge"], 1000000)

    def test_missing_weights(self):
        with self.assertRaises(FileNotFoundError):
            HfBackend(str(Path(__file__).resolve().parent), {}).load()


if __name__ == "__main__":
    unittest.main()
