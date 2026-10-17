# Agent C — Local visual model

Read AGENTS.md and implementation sections 3, 4, 7–9, 12–13. Own src/cashwatch/inference/, local prompt templates and inference tests. Propose models/manifest.json changes to the integrator; do not modify shared lockfiles.

Run a hardware/model feasibility spike immediately. Start with pinned Qwen3-VL-4B-Instruct and a compatible pinned Transformers/PyTorch stack. Load once in a resident process. Use only local paths, local_files_only, offline flags and a denied-egress validation. Keep decoder/sample inputs local. No provider APIs, auto-download at runtime, audio or identifying faces.

Implement typed observe and verify adapters: ordered frame IDs with exact timestamps → compact validated observations/predicates. For irregular samples, preserve explicit timing rather than pretending they are a constant-FPS native video. Respect actual processor visual-token budgets. Return supported/contradicted/unknown, references and limitations, never final legal citations or unrestricted verdict prose.

Use bounded generation, deterministic baseline, controlled parse repair and meaningful errors for missing files/OOM/cancellation. Reject unknown frame IDs, malformed intervals and out-of-range timestamps. Do not treat agreement between two calls of the same model as independent evidence.

Benchmark longest authorized clip/windows, output token sizes, peak memory and latency on the actual host; acquire the shared GPU reservation first. Distinguish synthetic timing from domain accuracy. Compare 8B or MLX only if the baseline gate warrants it. Document quantization/conversion provenance and licenses. No claimed five-minute guarantee without end-to-end measurement.

Handoff: exact model revision/files, processor version, local load command, observation schema compatibility, memory/latency table with sample counts, offline results, failure behavior and instructions for the resident worker. Restricted scene content stays local.
