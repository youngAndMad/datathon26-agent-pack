"""CUDA loading contracts with synthetic mocks; no GPU, weights or private media."""
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from cashwatch.inference.backends.hf_backend import HfBackend


class CudaNF4Tests(unittest.TestCase):
    def setUp(self):
        self.torch, self.transformers = MagicMock(), MagicMock()
        self.torch.__version__ = self.transformers.__version__ = "synthetic"
        self.torch.cuda.is_available.return_value = True
        self.torch.device.side_effect = lambda value: SimpleNamespace(type=value.split(":")[0])
        self.loader = self.transformers.Qwen3VLForConditionalGeneration.from_pretrained
        self.loader.return_value.is_loaded_in_4bit = True
        self.loader.return_value.hf_device_map = {"": "cuda:0"}
        for ctx in (
            patch.dict(sys.modules, {"torch": self.torch, "transformers": self.transformers}),
            patch.dict(os.environ),
            patch("sys.platform", "win32"),
            patch("cashwatch.inference.backends.hf_backend.check_model_dir", return_value=Path("synthetic-model")),
        ):
            ctx.start()
            self.addCleanup(ctx.stop)

    def load(self, **options):
        return HfBackend("synthetic-model", {"device": "cuda:0", "cuda_nf4": True, **options}).load()

    def test_nf4_is_offline_fp16_double_quant_with_fixed_cuda_placement(self):
        # Give this device realistic string serialization for placement assertions.
        class Device:
            type = "cuda"
            def __str__(self):
                return "cuda:0"
        self.torch.device.return_value = Device()
        self.torch.device.side_effect = None
        info = self.load()
        self.transformers.BitsAndBytesConfig.assert_called_once_with(
            load_in_4bit=True, bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=self.torch.float16,
        )
        kwargs = self.loader.call_args.kwargs
        self.assertEqual(kwargs["device_map"], {"": "cuda:0"})
        self.assertEqual(kwargs["dtype"], self.torch.float16)
        self.assertIs(kwargs["local_files_only"], True)
        self.assertIs(kwargs["trust_remote_code"], False)
        self.assertEqual(kwargs["quantization_config"], self.transformers.BitsAndBytesConfig.return_value)
        self.assertEqual(info["quantization"], "nf4-double")
        self.assertTrue(info["cuda_nf4"])
        self.assertFalse(info["windows_int8"])
        self.assertFalse(info["disk_offload"])
        self.assertEqual(os.environ["HF_HUB_OFFLINE"], "1")

    def test_conflicts_rejected_before_processor_or_model_loading(self):
        for options in (
            {"device": "cpu"}, {"device": "auto"}, {"device": "mps"},
            {"windows_int8": True}, {"max_memory": {"cpu": "2GiB"}},
            {"offload_dir": "unused"}, {"dtype": "bfloat16"},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.load(**options)
        self.loader.assert_not_called()
        self.transformers.AutoProcessor.from_pretrained.assert_not_called()

    def test_missing_cuda_fails_without_cpu_fallback(self):
        self.torch.cuda.is_available.return_value = False
        with self.assertRaisesRegex(RuntimeError, "CUDA unavailable"):
            self.load()
        self.loader.assert_not_called()
        self.transformers.AutoProcessor.from_pretrained.assert_not_called()

    def test_oom_propagates_without_reload(self):
        self.loader.side_effect = RuntimeError("CUDA out of memory")
        with self.assertRaisesRegex(RuntimeError, "out of memory"):
            self.load()
        self.assertEqual(self.loader.call_count, 1)

    def test_nonquantized_model_rejected(self):
        self.loader.return_value.is_loaded_in_4bit = False
        with self.assertRaisesRegex(RuntimeError, "not loaded in 4-bit"):
            self.load()

    def test_cpu_or_disk_spill_rejected(self):
        for target in ("cpu", "disk"):
            self.loader.return_value.hf_device_map = {"model": "cuda:0", "head": target}
            with self.subTest(target=target), self.assertRaisesRegex(RuntimeError, "refuses CPU or disk"):
                self.load()

    def test_cpu_default_does_not_construct_quantization_config(self):
        info = self.load(device="cpu", cuda_nf4=False)
        self.transformers.BitsAndBytesConfig.assert_not_called()
        self.assertNotIn("quantization_config", self.loader.call_args.kwargs)
        self.assertFalse(info["cuda_nf4"])


if __name__ == "__main__":
    unittest.main()
