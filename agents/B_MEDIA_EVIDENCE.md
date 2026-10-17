# Agent B — Media timeline and evidence

Read AGENTS.md and implementation sections 4, 8–9. Own src/cashwatch/media/ and tests/media/. Do not edit inference code or root dependencies; request them.

Implement probe_video, sample_video and extract_evidence against frozen DTOs. Safely decode valid MOV variants, honor display rotation, VFR, presentation order, stream offsets and edit lists. Record source SHA-256, original PTS and rational time base, normalized presentation time, source frame ID and approved transform. Never calculate the authoritative timestamp from nominal FPS.

Provide deterministic nearest-frame selection, coverage endpoints and accurate seek/decode-forward extraction. Cache source metadata/sampled images per job to avoid repeated full decode. Keep full original evidence distinct from model thumbnails. Export actual JPEGs with no AI reconstruction, preserve context and record transforms. A selected frame's actual time must be returned, not the requested target time.

Supply synthetic MOV tests with visible frame counters and timing landmarks, including VFR/rotation/offset/B-frame/edit-list fixtures where supported. These are test assets, not bank evidence. Verify time round-trip and anchor-to-evidence equality. Report codec/preview limitations; do not transcode away the original evidence authority. Preview generation must be optional and bounded.

Add safe decoder subprocess timeouts/resource guards and local-file-only behavior. No audio extraction, cloud OCR or network sources. Handoff includes supported formats, fixture commands, timing tolerances, extraction memory/time and T03–T07 results. Coordinate FrameRef with inference and reporting before implementation.
