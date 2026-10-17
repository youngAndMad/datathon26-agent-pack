"""Tiny synthetic weights only; no model downloads or competition data."""
import gc
from pathlib import Path
import sys
import unittest
import sys as _sys

import pytest

pytestmark = pytest.mark.skipif(_sys.platform != "win32", reason="Windows-only test (ACL / int8 qengine); fails on macOS on its own commit too")  # WINDOWS_ONLY

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import torch
from accelerate import init_empty_weights
from cashwatch.inference.backends.windows_int8 import _fill_model


class Source:
    def __init__(self, tensors):
        self.tensors = tensors

    def keys(self):
        return self.tensors.keys()

    def get(self, name):
        return self.tensors[name]


class Tiny(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.embedding = torch.nn.Embedding(32, 16)
        self.hidden = torch.nn.Linear(16, 16)
        self.lm_head = torch.nn.Linear(16, 32, bias=False)
        self.lm_head.weight = self.embedding.weight
        self.register_buffer("position", torch.arange(16, dtype=torch.float32), persistent=False)

    def forward(self, ids):
        return self.lm_head(self.hidden(self.embedding(ids)))


class Int8Tests(unittest.TestCase):
    def setUp(self):
        torch.set_num_threads(4)
        torch.manual_seed(42)
        self.reference = Tiny().eval()
        self.weights = {k: v.detach().clone() for k, v in self.reference.state_dict().items()
                        if k != "lm_head.weight"}

    def skeleton(self):
        with init_empty_weights(include_buffers=False):
            model = Tiny()
        # accelerate's register_parameter patch can leave tied aliases distinct;
        # restore the architecture's explicit sharing after the context.
        model.lm_head.weight = model.embedding.weight
        return model

    def test_streaming_packed_head_and_float_embedding(self):
        model = _fill_model(self.skeleton(), Source(self.weights))
        ids = torch.tensor([[1, 2, 3]])
        with torch.inference_mode():
            expected = self.reference(ids)
            actual = model(ids)
        self.assertLess((actual - expected).abs().max().item(), 0.15)
        self.assertEqual(model.lm_head.weight().dtype, torch.qint8)
        self.assertEqual(model.embedding.weight.dtype, torch.bfloat16)
        self.assertEqual(model.embedding(ids).dtype, torch.float32)
        self.assertFalse(any(p.is_meta for p in model.parameters()))
        self.assertFalse(model.position.is_meta)
        self.assertEqual(model._cashwatch_int8_linear_count, 2)

    def test_missing_parameter_fails(self):
        self.weights.pop("hidden.bias")
        with self.assertRaisesRegex(ValueError, "missing"):
            _fill_model(self.skeleton(), Source(self.weights))

    def test_unexpected_parameter_fails(self):
        self.weights["invented"] = torch.zeros(1)
        with self.assertRaisesRegex(ValueError, "Unexpected"):
            _fill_model(self.skeleton(), Source(self.weights))

    def test_shape_mismatch_fails(self):
        self.weights["hidden.weight"] = torch.zeros(2, 2)
        with self.assertRaisesRegex(ValueError, "shape mismatch"):
            _fill_model(self.skeleton(), Source(self.weights))

    def test_tiny_qwen_text_generation(self):
        from transformers import Qwen3VLConfig, Qwen3VLForConditionalGeneration
        config = Qwen3VLConfig(tie_word_embeddings=True,
            text_config={"vocab_size": 32, "hidden_size": 16, "intermediate_size": 32,
                         "num_hidden_layers": 1, "num_attention_heads": 2,
                         "num_key_value_heads": 2, "head_dim": 8,
                         "max_position_embeddings": 128, "tie_word_embeddings": True,
                         "rope_parameters": {"rope_type": "default", "mrope_section": [1, 1, 2]}},
            vision_config={"depth": 1, "hidden_size": 16, "intermediate_size": 32,
                           "num_heads": 2, "patch_size": 2, "temporal_patch_size": 1,
                           "spatial_merge_size": 1, "out_hidden_size": 16,
                           "num_position_embeddings": 16, "deepstack_visual_indexes": [0]},
            image_token_id=30, video_token_id=31, vision_start_token_id=29,
            vision_end_token_id=28)
        reference = Qwen3VLForConditionalGeneration(config).eval()
        weights = {k: v.detach().clone() for k, v in reference.state_dict().items() if k != "lm_head.weight"}
        with init_empty_weights(include_buffers=False):
            skeleton = Qwen3VLForConditionalGeneration(config)
        skeleton.tie_weights()
        model = _fill_model(skeleton, Source(weights))
        with torch.inference_mode():
            generated = model.generate(input_ids=torch.tensor([[1, 2, 3]]),
                                       max_new_tokens=2, do_sample=False, pad_token_id=0,
                                       eos_token_id=27)
            multimodal = model.generate(input_ids=torch.tensor([[29, 30, 30, 30, 30, 28, 1]]),
                                        pixel_values=torch.randn(4, 12),
                                        image_grid_thw=torch.tensor([[1, 2, 2]]),
                                        max_new_tokens=2, do_sample=False, pad_token_id=0,
                                        eos_token_id=27)
        self.assertGreater(generated.shape[1], 3)
        self.assertGreater(multimodal.shape[1], 7)
        self.assertFalse(any(p.is_meta for p in model.parameters()))
        self.assertFalse(any(b.is_meta for b in model.buffers()))


if __name__ == "__main__":
    unittest.main()
