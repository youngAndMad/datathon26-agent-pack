"""Synthetic multi-frame CUDA check. Does not read competition data or change defaults."""
from __future__ import annotations

import argparse
import json
import os
import uuid
from pathlib import Path

from cashwatch.gpulock import gpu_reservation
from cashwatch.inference import adapter_config_from_profile, create_perception
from cashwatch.settings import REPO_ROOT, Settings
from model_bench import run_calls


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--frames', type=int, default=8)
    ap.add_argument('--pixels', type=int, default=384)
    ap.add_argument('--observe-tokens', type=int, default=128)
    args = ap.parse_args()
    if not 1 <= args.frames <= 16 or not 128 <= args.pixels <= 480 or not 64 <= args.observe_tokens <= 512:
        ap.error('unsupported benchmark size')
    os.environ.update(CASHWATCH_WINDOWS_CUDA_NF4='1', CASHWATCH_WINDOWS_CPU_INT8='0',
                      CASHWATCH_WINDOWS_CPU_LOW_MEMORY='0', HF_HUB_OFFLINE='1',
                      TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
    settings = Settings(mode='test', backend='transformers', device='cuda',
                        model_path=REPO_ROOT/'models/qwen3-vl-2b-instruct',
                        model_manifest=REPO_ROOT/'models/manifest.windows-2b.json')
    folder = REPO_ROOT/'data/bench'/('windows-cuda-'+uuid.uuid4().hex[:10])
    folder.mkdir(parents=True)
    report = {'synthetic_only':True, 'domain_accuracy_measured':False, 'calls':[]}
    with gpu_reservation('windows-cuda-synthetic-benchmark'):
        for kind, pixels, tokens in [('observe', args.pixels, args.observe_tokens),
                                      ('verify', min(480, args.pixels), 192)]:
            adapter = create_perception(settings, config=adapter_config_from_profile(
                settings.profile, max_long_side_px=pixels, frame_label_mode='index'))
            try:
                adapter.warmup(timeout_sec=180)
                row = run_calls(adapter, kind, pixels, args.frames, tokens, 1, 1.0,
                                folder, 0 if kind == 'observe' else 3)
                report['calls'].append(row)
                (folder/'summary.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            finally:
                adapter.close()
    print(json.dumps({'report':str(folder/'summary.json'), 'calls':[
        {'kind':r['kind'], 'latency_sec':r['latency_sec'], 'failed':r['failed']}
        for r in report['calls']]}))
    return 1 if any(r['failed'] for r in report['calls']) else 0


if __name__ == '__main__':
    raise SystemExit(main())
