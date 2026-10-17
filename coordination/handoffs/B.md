# Handoff: agent B (media timeline and evidence)

Status: ready_for_integration. All work used SYNTHETIC media only. No competition or personal video was opened.

## Owned files changed
- `src/cashwatch/media/__init__.py`: exposes `create_media(**overrides) -> MediaPort` (a `MediaService`)
- `src/cashwatch/media/service.py`: the MediaPort implementation. Contains the ffprobe-based probe, limits, the guarded child dispatch and the preview.
- `src/cashwatch/media/decode.py`: PyAV core. Covers the presentation index, sequential selection pass, seek + decode-forward, rendering, motion and evidence.
- `src/cashwatch/media/_worker.py`: guarded decoder child (`python -m cashwatch.media._worker`, JSON over stdin/stdout)
- `src/cashwatch/media/paths.py`: local-file-only validation, streamed SHA-256, containment, symlink-refusing atomic writes
- `src/cashwatch/media/tools.py`: ffprobe/ffmpeg discovery and bounded subprocess runner (argument lists, `-protocol_whitelist file`, `file:` URLs, minimal env)
- `scripts/make_synthetic_media.py`: fixture generator, frame barcode encoder/decoder, ground-truth builder
- `tests/media/{__init__,conftest,test_probe_failures,test_timeline,test_evidence_preview_motion}.py`
- `coordination/requests/B.md`, this file

## Functionality completed
- **probe_video**
  - Validation: rejects URLs and protocol prefixes, non-regular files and empty files.
  - ffprobe subprocess with timeout. Container must be the `mov,mp4,m4a,3gp,3g2,mj2` demuxer, otherwise `unsupported_container`.
  - Takes the first non-attached-picture video stream. Audio is recorded in `has_audio` and never read: the stream is discarded at the demuxer.
  - Decode-bomb guards raise `limits_exceeded`. They cover each dimension (max 8192), pixel area (max 3840*2160), header and index frame count (max 36 000), and duration (max 3600 s).
  - Display rotation comes from the display matrix or legacy `rotate` tag. It is stored as clockwise degrees, i.e. the rotation a player applies. A display matrix of -90 gives `display_rotation=90`.
  - The SAR is applied to the display dimensions. The SHA-256 is streamed.
  - A guarded PyAV pass builds the presentation index from non-discarded packets: edit-list pre-roll is flagged discard by libavformat, the same as in ffprobe/ffmpeg. It then decodes the first presented frame and the last indexed frame.
  - Timeline origin: `origin_pts` is the first decoded presented PTS (`origin_method="first_decoded_pts"`).
  - Failure codes:
    - Missing PTS, duplicate PTS or a non-monotonic timeline raise `timeline_unsupported`.
    - Truncated data, decode failure or an unreachable last frame raise `decode_failed`.
    - An unparseable container raises `invalid_media`.
  - `duration_sec` = last frame + its duration. `nominal_fps` is diagnostic only.
  - `decode_warnings` record VFR, edit-list pre-roll, nonzero stream start, audio ignored and SAR.
- **sample_video**
  - The source hash is re-verified first: a source swapped after probe raises `invalid_media`.
  - It then makes one sequential, frame-threaded decode pass in presentation order.
  - Selection: each requested time maps to the first presented frame with `(pts-origin)*tb >= target`, using exact `Fraction` arithmetic; a target beyond the end maps to the last frame. Frames are never relabelled.
  - Thumbnails are aspect-preserving, with long side `model_long_side_px` (never upscaled). Display rotation is applied, the transforms are recorded, and files are written as JPEG q90 to `work_dir/frames/<frame_id>_L<px>.jpg` with their sha256.
  - Frames shared by windows appear once. `window_frames` holds ordered ids. Coverage values are the actual first and last sampled times.
  - When it decodes to EOF, it checks that the decoder presented as many frames as the index and that the last frame time matches the probe.
  - It checks the deadline every 24 frames and computes the 4 fps motion profile in the same pass (cached).
- **frames_for_times**: same selection rule, applied through the index. Uses keyframe seek and decodes forward to the exact PTS. If a seek overshoots (open-GOP leading frames, edit-list pre-roll), it retries from earlier positions (1, 2, 4… s back, then from the start). Results are de-duplicated and in presentation order.
- **motion_profile**: mean absolute difference of 160 px grey frames sampled at the requested fps (same selection rule), divided by 255. It is a hint only. The first sample scores 0. The result is identical whether computed standalone or inside sample_video.
- **extract_evidence**
  - Checks before decoding: the FrameRef source hash, time base, PTS↔relative_sec and frame_id↔PTS must all match, and the PTS must be in the index, otherwise `evidence_invalid`.
  - It then re-decodes the exact PTS by seeking and decoding forward, and asserts the decoded PTS is identical.
  - Output steps: colour conversion (matrix and range recorded), aspect-preserving downscale to ≤ `long_side_px` (no upscaling), display rotation, then a real JPEG at `jpeg_quality`. The JPEG is checked with PIL `verify()`.
  - Files go to `out_dir/evidence/<evidence_id>.jpg` (`evidence_id = "e" + frame_id`, `frame_id = "p<pts>"`, negative PTS gives `"pm<abs>"`). `jpeg_path` is relative and `timestamp_sec = dto.ts(relative_sec)`. Duplicate refs are de-duplicated and input order is kept.
- **resolve_frame**: containment check. Symlinked leaves and paths escaping `work_dir` are refused, and the file must exist.
- **make_preview**
  - Encoding: ffmpeg libx264 veryfast, CRF 26, no B-frames, GOP 30, `-an -sn -dn`, metadata stripped. Autorotate is applied, the long side is ≤ 1280, `-fps_mode passthrough`, faststart. Bounded by `min(max_sec, remaining-1)`.
  - Verification before an atomic rename: the first preview frame is at t=0, the frame count equals the source, and the last-frame time matches within one frame interval.
  - Returns `{"offset_sec":0.0,"codec":"h264",...}`, or `None` if it fails, has no budget, or ffmpeg is missing.
- **Robustness / hang mitigation**
  - Every PyAV operation (probe index, sample, motion, refine, evidence) runs in a fresh child process. The parent uses `subprocess.run(timeout=budget+grace)` and kills the child on timeout.
  - A timeout maps to `deadline_exceeded` if the job deadline expired, otherwise `decode_failed`. A crash (for example SIGSEGV) maps to `decode_failed`.
  - The child disables core dumps and keeps only a minimal environment. ffprobe and ffmpeg have the same bounds.
  - Libav decode errors are tolerated per packet up to 16, then the call fails.
  - `create_media(isolate=False)` runs the identical code in-process; parity is tested. Child start-up costs about 0.2 s per call.

## Contracts consumed
`cashwatch.dto` (VideoMetadata, FrameRef, SamplingPlan/Window, FrameManifest, MotionSample, EvidenceFrame, Transform, `ts`), `cashwatch.ports.MediaPort` (`isinstance` check passes), `cashwatch.deadline.Deadline`, `cashwatch.errors.PipelineError`. No frozen file was modified.

## Verification commands and outcomes
- `.venv/bin/python scripts/make_synthetic_media.py --force`: builds all 22 fixtures in 45 s wall on M4 Pro. On a first test run, missing fixtures are built automatically.
- `.venv/bin/python -m pytest tests/media -q`: **48 passed** in about 27–31 s with fixtures present.

What the tests cover:
- **T03**: empty → invalid_media; PNG renamed → unsupported_container; MKV renamed → unsupported_container; random bytes → invalid_media; moov missing → invalid_media; truncated faststart → decode_failed; directory, URL, `file:` prefix or missing path → invalid_media; valid MOV renamed `.bin` → accepted.
- **T04**: 8192x4352 → limits_exceeded; frame-count guard and configurable pixel-area guard; zero probe budget; sampling deadline → deadline_exceeded; hung decoder child killed within the budget; hung ffprobe bounded; crashing child typed.
- **T05**: 10 s clips at 29.97, 30 and 60 fps, plus 145 s 1080p 29.97. Checks:
  - every requested time maps to exactly the ground-truth frame (first ffprobe frame ≥ target);
  - the selected time is ≥ target and ≤ target + the preceding frame gap;
  - first and last intervals are covered; beyond-end targets map to the last frame;
  - thumbnail barcode = ground-truth frame; ordinal = presentation index;
  - the sampling call takes under 20 s.
- **T06**: VFR, nonzero start (empty edit, 7.25 s), x264 B-frames with b-pyramid, stream-copy edit list starting mid-GOP (first presented frame = source frame 75, 16 pre-roll packets discarded), HEVC hvc1 with open GOP, cw90 and ccw90 rotation, audio track. Checks:
  - probe origin, frame count and last time equal ffprobe;
  - sampled times equal ffprobe `pts*tb`;
  - seek refinement selects the same frames as the rule;
  - rotation matches ffmpeg autorotate pixels;
  - evidence round-trip decodes the same barcode/PTS;
  - preview frame 0 shows the source origin frame at t=0, and frame 30 matches.
- **T07**: evidence JPEGs decode (SOI marker, PIL), the size is the long side (≤1280, no upscaling), the sha256 matches, `timestamp_sec == ts(relative_sec)`, base64 round-trips, and transforms are recorded. Foreign hash, non-frame PTS, relabelled time and wrong frame_id all raise evidence_invalid.
- Also covered: motion spike isolated at exactly t=20.0 s on the marker twins; marker fixture pixel truth (square inside until 19.967 s, outside from 20.000 s; negative twin never leaves); resolve_frame symlink and missing-file refusal.

## Measured latency / memory
Host: Apple M4 Pro (14 cores), 48 GB, macOS 26.6. Isolated child mode (production default), software decode. Fixture `cfr_145s_2997_1080p` (H.264 1920x1080, 29.97 fps, 4346 frames, 10 Mbit/s, 181 MB, B-frames, GOP 60). Plan: 12 s windows with 3 s overlap at 1 fps (222 requested times → 146 frames, thumbnails at 640 px). **n = 3 runs**:

| Operation | Median | Max | Child peak RSS |
|---|---|---|---|
| probe_video (ffprobe + index + first/last decode + SHA-256) | 0.44 s | 0.46 s | 275 MB |
| sample_video (full decode about 1900 fps, 146 thumbnails, 580 motion samples) | 2.54 s | 2.56 s | 201 MB |
| motion_profile standalone (4 fps) | about 2.3 s | | 184 MB |
| extract_evidence, 3 frames at 1280 px q88 | 0.39 s | 0.46 s | 196 MB |
| frames_for_times, 24 frames at 4 fps | 0.45 s | 0.46 s | |
| make_preview (libx264 veryfast, 720p) | 3.80 s | 3.87 s | 408 MB (ffmpeg) |

The parent process peak was 82 MB.

## Supported formats and timing tolerances
- **Container**: QuickTime/MP4 demuxer only (`.mov`, or `.mp4` content under any extension).
- **Codecs**: any codec libavcodec decodes in software; tested with H.264 and HEVC (hvc1). Rotation must be 0, 90, 180 or 270.
- **Timing**: timestamps are exact `(pts - origin_pts) * time_base`. On every fixture they match ffprobe within 1e-9 s. A selected frame is at most one preceding frame interval after the requested time. Evidence `timestamp_sec` is rounded to 3 decimals (D007).

## Limitations / assumptions
- **Origin with an empty edit**: with an empty edit or stream offset, zero is the first presented frame and the leading empty interval is not counted (same as the ffmpeg CLI). A warning is recorded.
- **Colour**: an unspecified colour matrix is converted as BT.601 and an unspecified range as limited (swscale defaults). Both are recorded in the transforms.
- **Frame skipping**: none, so decode cost scales with source bitrate and resolution. 4K or 60 fps input roughly doubles or quadruples the pass time. No VideoToolbox hardware decode is used.
- **Child memory**: not hard-limited, because RLIMIT_AS is not enforced on macOS. It is bounded by process lifetime and the frame-size and frame-count guards. A container/OS memory limit is recommended (F).
- **Hang guard**: a hang is detected only by the per-call budget, which is the remaining job deadline (probe uses `timeout_sec`). There is no separate watchdog.
- **Motion signal**: includes all visible change, including on-screen counters and overlays. It is a hint only.
- **Preview**: requires libx264 in ffmpeg. The 145 s fixture is 10 Mbit/s, heavier than the "about 29 MB" suggestion, as a conservative latency case.
- **Not tested**: real phone footage (blocked by D004). Synthetic success is not evidence of real-footage behaviour; the origin, rotation and VFR handling must be confirmed on authorised local clips by a local agent.

## Requested integration actions
See `coordination/requests/B.md`. In short:
- (R1) Optionally call sample_video before motion_profile to save one decode pass.
- (R2) Optionally pass Settings limits to `create_media`.
- (R4) F must bundle ffprobe/ffmpeg with libx264.
- (R5) Thumbnail path naming is informational.

Synthetic fixtures for the orchestrator's end-to-end test are `tests/fixtures/generated/marker_violation_40s.mov` (red square leaves the yellow zone at exactly t=20.000 s, which is frame 600) and `marker_negative_40s.mov`. Build them with `.venv/bin/python scripts/make_synthetic_media.py --only marker_violation_40s --only marker_negative_40s`. Each fixture has a `<name>.truth.json` with ffprobe PTS and barcodes.
